#!/usr/bin/env python3
"""Copy the reviewed Graph52 build originals without claiming another build.

The fixed capsule is a partial original snapshot: eight zlib ELF originals,
their actual build command/streams, original sources, API/deadline and kernel
observations. It carries no owner state, ticket, credential or Native authority.
Current source comparison and current exclusive binary copies are separate
observations. Only the four never-started continuation cases may use this path.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tarfile
import time
import zlib

MIB = 1024 * 1024
CHUNK = MIB
SELECTOR = "unstarted-after-42372165893"
ORIGIN = {"source_sha": "52d149ea0dd5550fbded28ad0ea337353f826210",
          "semwright_sha": "acc0a089e2f0e3e9936632fb2a4aa0c2fab56199",
          "run_id": "42356210001", "run_attempt": "1"}
ORIGIN_TREE = "cece8cde18950b2cb7d15ce8c3eeec179e72b905"
SDK_TREE = "39cd9576c4a3213b186c046e146acbbab4fa2e14"
ORIGIN_INDEX_SHA = "0cd8c7286997286101fbdf09f3970b99e9a3ed1426ca5b40f0384bf4382c1641"
ORIGIN_REVIEW_SHA = "92cf969c4b0f5f518a43c8e8db251af992a39d15fbff94157bc5d222ef9c3af3"
CAPSULE = {"bytes": 90163200, "sha256": "a93d94623b2ec11298476bc529c714e8e239fa4bfb2701a9e7f9cb771f10f072"}
MANIFEST_SHA = "ede134824cf9318c5efe6bce1c2100b16f4150be51071cad7f930943804664b8"
MANIFEST_BYTES = 25905
MEMBERS = 124  # manifest.json plus its 123 exact retained members.
PAYLOAD_BYTES = 90031737
ROLES = ("semwright", "semwrightd", "semwright_sandbox", "native_provider",
         "shared_state_actor", "manim_runner", "ordinary_service", "native_graph_session")
SDK_ROLES = ROLES[:3]
MAX_FILE = 256 * MIB
MAX_METADATA = 2 * MIB
MAX_ELF_TOTAL = 512 * MIB
SCHEMA = "motionwright.graph.original-build-reuse/1"

# The closed Git delta is reviewed as non-inputs to these eight targets. All
# other tracked blobs, including Cargo/lock/toolchain, build scripts, native
# libraries, the selected examples and Graph's embedded profile, must be equal.
# These five Rust/example-test files belong only to native-manim-e2e/tests;
# Graph52's actual compiler-artifact targets select none of them.
UNCOMPILED_MANIM_PATHS = frozenset((
    "crates/native/examples/native-manim-e2e.rs",
    "crates/native/examples/native_manim_editable/recovery.rs",
    "crates/native/examples/native_manim_editable/reference.rs",
    "crates/native/examples/native_manim_editable/source_cycle.rs",
    "crates/native/tests/fixtures/manim-n1-receipt-lifecycle-42369864661.json",
))
NONCOMPILED_CHANGED_PATHS = frozenset((
    ".github/workflows/native-manim-e2e.yml", ".github/workflows/studio-service-verification.yml",
    "apps/studio/tests/semantic-graph-service.spec.ts", "apps/studio/tests/support/graph-owner/producer_guard.mjs",
    "apps/studio/tests/support/nativeGraphCase.ts", "apps/studio/tooling/graph-owner/continuation.test.mjs",
    "tooling/ci_canvas_service.py", "tooling/native_graph_negative_evidence.py",
    "tooling/native_graph_build_reuse.py", "tooling/native_graph_build_transfer.py",
    "tooling/native_manim_e2e.py", "tooling/native_manim_editable_outer.py",
    "tooling/native_manim_frozen_runtime.py", "tooling/native_manim_frozen_runtime_host.py",
    "tooling/native_manim_frozen_runtime_restore.py", "tooling/native_manim_recovery_custody.py",
    "tooling/native_manim_recovery_cycle.py", "tooling/native_manim_recovery_gate.py",
    "tooling/native_manim_recovery_lineage.py", "tooling/native_manim_resume_custody.py",
    "tooling/native_manim_resume_cycle.py", "tooling/native_manim_resume_focals.py",
    "tooling/native_manim_resume_host.py", "tooling/native_manim_resume_lineage.py",
    "tooling/native_manim_resume_pixels.py", "tooling/native_manim_resume_resources.py",
    "tooling/native_manim_resume_runtime.py", "tooling/native_manim_resume_transfer.py",
    "tooling/native_manim_retained_reference.py",
    "tooling/requirements/manim-frozen-runtime-42361964881-artifact.json",
    "tooling/requirements/manim-frozen-runtime-42361964881-receipt.json",
    "tooling/requirements/manim-retained-reference-42369864661.json",
    "tooling/requirements/manim-runtime-observed-42357390508.json",
    "tooling/tests/fixtures/manim-recovery-42365799758.stdout.txt",
    "tooling/tests/fixtures/manim-sdk-guard-42365799758.json",
    "tooling/tests/test_ci_canvas_service.py", "tooling/tests/test_ci_manim_source_selection.py",
    "tooling/tests/test_graph_continuation.py", "tooling/tests/test_graph_negative_evidence.py",
    "tooling/tests/test_graph_build_reuse.py", "tooling/tests/test_graph_build_transfer.py",
    "tooling/tests/test_native_manim_frozen_runtime.py", "tooling/tests/test_native_manim_recovery.py",
    "tooling/tests/test_native_manim_recovery_lineage.py", "tooling/tests/test_native_manim_resume.py",
    "tooling/tests/test_native_manim_retained_reference.py", "tooling/tests/test_native_manim_runtime_restore.py",
)) | UNCOMPILED_MANIM_PATHS


class Rejected(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Rejected("graph_build_reuse_" + code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_METADATA, "metadata_bound")
    def pairs(rows):
        result = {}
        for name, value in rows:
            require(name not in result, "duplicate_json_key")
            result[name] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Rejected("nonfinite_json")))


def signature(info):
    mode = info.st_mode & ~0o111 if os.name == "nt" and stat.S_ISREG(info.st_mode) else info.st_mode
    return (info.st_dev, info.st_ino, mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))


def namespace(path, directory=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, "canonical_path")
    for entry in (*reversed(path.parents), path):
        state = entry.lstat()
        require(not stat.S_ISLNK(state.st_mode) and not getattr(state, "st_file_attributes", 0) & 0x400,
                "alias_or_reparse")
        require(stat.S_ISDIR(state.st_mode) if entry != path or directory else
                stat.S_ISREG(state.st_mode) and state.st_nlink == 1, "regular_single_link")
    return signature(path.lstat())


@contextmanager
def stable_reader(path, maximum):
    # Same stable-handle boundary already exercised by frozen_runtime_host,
    # including NT executable-bit normalization and nonblocking FIFO rejection.
    path = Path(path); before = namespace(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        observed = os.fstat(fd)
        require(signature(observed) == before and 0 <= observed.st_size <= maximum, "handle_identity_or_bound")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream, observed.st_size
        require(signature(os.fstat(fd)) == before and namespace(path) == before, "changed_during_read")
    finally:
        os.close(fd)


def hash_stream(stream, maximum, guard=lambda: None):
    count, hashed = 0, hashlib.sha256()
    while block := stream.read(CHUNK):
        guard(); count += len(block)
        require(count <= maximum, "stream_bound")
        hashed.update(block)
    return {"bytes": count, "sha256": hashed.hexdigest()}


def identity(path, maximum=MAX_FILE, guard=lambda: None):
    with stable_reader(path, maximum) as (stream, size):
        result = hash_stream(stream, maximum, guard)
        require(result["bytes"] == size, "physical_size_changed")
    return result


def read_small(path, maximum=MAX_METADATA):
    with stable_reader(path, maximum) as (stream, size):
        raw = stream.read(maximum + 1)
        require(len(raw) == size, "small_file_changed")
    return raw


def new_bytes(path, raw):
    path = Path(path); namespace(path.parent, True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as out:
            require(out.write(raw) == len(raw), "short_write")
            out.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    require(identity(path, max(MAX_METADATA, len(raw))) == {"bytes": len(raw), "sha256": sha(raw)}, "write_readback")
    return {"path": str(path), "size_bytes": len(raw), "sha256": sha(raw)}


def new_json(path, value):
    return new_bytes(path, encoded(value))


def relative(name):
    require(type(name) is str and 0 < len(name) <= 512 and "\\" not in name
            and not name.startswith("/") and all(part not in ("", ".", "..") for part in name.split("/"))
            and all(32 <= ord(c) < 127 for c in name), "relative_member")
    return name


def checked_manifest(raw):
    require(sha(raw) == MANIFEST_SHA, "original_manifest_digest")
    value = strict(raw)
    require(value["schema"] == "motionwright.graph.origin-build-capsule/1"
            and encoded(value["origin"]) == encoded(ORIGIN)
            and value["origin_source_tree"] == ORIGIN_TREE and value["origin_sdk_tree"] == SDK_TREE
            and value["origin_index_sha256"] == ORIGIN_INDEX_SHA and value["origin_review_sha256"] == ORIGIN_REVIEW_SHA
            and value["roles"] == list(ROLES) and value["current_context"] is None
            and value["original_snapshot_complete"] is False and value["current_source_or_abi_verified"] is False
            and value["owner_state_included"] is False and value["owner_authority"] is False
            and value["runtime_tree_included"] is False, "original_manifest_scope")
    rows = value["members"]
    require(type(rows) is list and len(rows) == MEMBERS - 1
            and sum(row["bytes"] for row in rows) == PAYLOAD_BYTES, "original_member_roster")
    names = [relative(row["path"]) for row in rows]
    require(len(set(names)) == len(names) and "manifest.json" not in names
            and set(value["binaries"]) == set(ROLES)
            and sum(value["binaries"][role]["size_bytes"] for role in ROLES) <= MAX_ELF_TOTAL,
            "original_binary_roster")
    return value


def member_fields(member):
    require(member.isfile() and not member.linkname and member.sparse is None
            and member.mode == 0o600 and member.uid == member.gid == member.mtime == 0
            and member.uname == member.gname == "" and set(member.pax_headers) <= {"path"}, "regular_tar_member")
    relative(member.name)


def retain_capsule(path, destination, *, guard=lambda: None):
    """One exclusive extraction of the fixed 124-member capsule; no ELF exec."""
    destination = Path(destination)
    require(identity(path, CAPSULE["bytes"], guard) == CAPSULE, "capsule_identity")
    destination.mkdir(mode=0o700, exist_ok=False)
    new_json(destination / "intake-intent.original.json", {"capsule": CAPSULE, "origin": ORIGIN,
        "native_authority": False, "automatic_retry": False})
    observed = {}
    try:
        with stable_reader(path, CAPSULE["bytes"]) as (stream, size), tarfile.open(fileobj=stream, mode="r|") as archive:
            first = archive.next(); require(first is not None and first.name == "manifest.json", "manifest_first")
            member_fields(first); require(first.size <= MAX_METADATA, "manifest_size")
            raw = archive.extractfile(first).read(MAX_METADATA + 1)
            require(len(raw) == first.size, "manifest_read")
            manifest = checked_manifest(raw)
            last_end = first.offset_data + ((first.size + 511) // 512) * 512
            expected = {row["path"]: {key: row[key] for key in ("bytes", "sha256")} for row in manifest["members"]}
            new_bytes(destination / "manifest.json", raw)
            while (member := archive.next()) is not None:
                guard(); member_fields(member)
                require(member.name in expected and member.name not in observed
                        and member.size == expected[member.name]["bytes"], "member_identity_or_duplicate")
                target = destination / relative(member.name)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True); namespace(target.parent, True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
                total, hashed = 0, hashlib.sha256()
                try:
                    with os.fdopen(fd, "wb", closefd=False) as output, archive.extractfile(member) as source:
                        while block := source.read(CHUNK):
                            guard(); total += len(block)
                            require(total <= member.size, "member_read_bound")
                            require(output.write(block) == len(block), "member_short_write"); hashed.update(block)
                        output.flush(); os.fsync(fd)
                finally:
                    os.close(fd)
                actual = {"bytes": total, "sha256": hashed.hexdigest()}
                require(actual == expected[member.name] and identity(target, member.size, guard) == actual,
                        "retained_member_readback")
                observed[member.name] = actual
                last_end = member.offset_data + ((member.size + 511) // 512) * 512
            require(set(observed) == set(expected), "exact_original_members_missing")
            require(size == ((last_end + 1024 + 10239) // 10240) * 10240,
                    "original_tar_record_padding")
            stream.seek(last_end)
            remaining = size - last_end
            require(remaining >= 1024, "original_tar_end_markers")
            while block := stream.read(CHUNK):
                guard(); require(not any(block), "original_tar_hidden_trailer")
                remaining -= len(block)
            require(remaining == 0, "original_tar_trailer_changed")
        index_raw = read_small(destination / "producer/producer-index.original.json")
        require(sha(index_raw) == ORIGIN_INDEX_SHA, "original_index_changed")
        new_json(destination / "intake-terminal.original.json", {"status": "ORIGINAL_BUILD_INPUTS_RETAINED",
            "capsule": CAPSULE, "members": MEMBERS, "origin": ORIGIN, "native_authority": False,
            "new_builds": 0, "automatic_retry": False})
        return manifest, strict(index_raw)
    except BaseException as error:
        try:
            new_json(destination / "intake-negative.original.json", {"status": "INCOMPLETE_NO_RETRY",
                "error_type": type(error).__name__, "retained_members": len(observed), "native_authority": False})
        except BaseException:
            pass
        raise


def source_comparison(origin_tree, current_tree, current_sha, changed, *, dirty=False):
    require(origin_tree == ORIGIN_TREE and re.fullmatch(r"[0-9a-f]{40}", current_tree)
            and re.fullmatch(r"[0-9a-f]{40}", current_sha) and current_sha != ORIGIN["source_sha"]
            and not dirty, "immutable_source_trees_required")
    require(type(changed) is list and changed == sorted(set(changed))
            and all(relative(name) in NONCOMPILED_CHANGED_PATHS for name in changed),
            "changed_compiled_input_rebuild_only_affected_target")
    return {"schema": "motionwright.graph.compiled-material-comparison/1", "origin_source": ORIGIN["source_sha"],
        "origin_tree": ORIGIN_TREE, "current_source": current_sha, "current_tree": current_tree,
        "unchanged_tracked_blobs_except": changed, "excluded_uncompiled_paths": sorted(set(changed) & UNCOMPILED_MANIM_PATHS),
        "selected_roles": list(ROLES), "sdk_sha": ORIGIN["semwright_sha"], "sdk_tree": SDK_TREE,
        "all_other_tracked_blobs_identical": True, "new_builds": 0, "native_authority": False}


def compare_git(current_sha, git):
    """The caller supplies its existing bounded, original-recording Git runner."""
    require(git("rev-parse", "HEAD").decode().strip() == current_sha, "source_head_changed")
    old_tree = git("rev-parse", ORIGIN["source_sha"] + "^{tree}").decode().strip()
    tree = git("rev-parse", current_sha + "^{tree}").decode().strip()
    raw = git("diff", "--name-only", "--no-renames", "-z", ORIGIN["source_sha"], current_sha)
    names = sorted(raw.decode("utf-8", errors="strict").rstrip("\0").split("\0")) if raw else []
    return source_comparison(old_tree, tree, current_sha, names,
                             dirty=bool(git("diff", "--name-only", "HEAD", "--")))


def elf_interpreter(path):
    """Read PT_INTERP from the retained ELF, without executing that program."""
    with stable_reader(path, MAX_FILE) as (stream, size):
        header = stream.read(64)
        require(len(header) == 64 and header[:7] == b"\x7fELF\x02\x01\x01"
                and header[18:20] == b"\x3e\x00", "ABI_ELF_header")
        phoff = struct.unpack_from("<Q", header, 32)[0]
        entry, count = struct.unpack_from("<HH", header, 54)
        require(entry == 56 and 0 < count <= 128 and 64 <= phoff <= size - count * entry,
                "ABI_program_header_bound")
        stream.seek(phoff); table = stream.read(count * entry)
        require(len(table) == count * entry, "ABI_program_headers")
        interpreters = []
        for offset in range(0, len(table), entry):
            if struct.unpack_from("<I", table, offset)[0] == 3:
                position = struct.unpack_from("<Q", table, offset + 8)[0]
                length = struct.unpack_from("<Q", table, offset + 32)[0]
                require(1 < length <= 4096 and 0 <= position <= size - length, "ABI_interpreter_bound")
                stream.seek(position); raw = stream.read(length)
                require(raw.endswith(b"\0") and raw.count(b"\0") == 1, "ABI_interpreter_string")
                interpreters.append(raw[:-1].decode("ascii", errors="strict"))
        require(interpreters == ["/lib64/ld-linux-x86-64.so.2"], "ABI_original_interpreter")
        return interpreters[0]


def release_verified_transport(intake, retained, comparison, current, guard=lambda: None):
    """Release ONLY two authenticated temporary copies, never any negative.

    The source capsule in the host's private E: directory and its encrypted
    draft asset remain untouched. Selected originals and their original index
    remain in the producer. Failure records the first error and is not retried.
    """
    intake, retained = Path(intake), Path(retained)
    require(intake.name == "intake" and intake.parent.name == "origin-transport"
            and retained == intake.parent.parent / "origin-build52", "release_exact_namespaces")
    terminal = strict(read_small(intake / "intake-terminal.original.json"))
    kept = strict(read_small(retained / "intake-terminal.original.json"))
    require(terminal["status"] == "AUTHENTICATED_ORIGINAL_BUILD_INPUTS"
            and encoded(terminal["context"]) == encoded(current) and terminal["capsule"] == CAPSULE
            and terminal["owner_authority"] is False and terminal["automatic_retry"] is False
            and kept["status"] == "ORIGINAL_BUILD_INPUTS_RETAINED" and kept["members"] == MEMBERS
            and kept["capsule"] == CAPSULE and kept["native_authority"] is False,
            "release_authentication_and_originals_required")
    require(encoded(comparison) == encoded(source_comparison(ORIGIN_TREE, comparison["current_tree"],
        current["source_sha"], comparison["unchanged_tracked_blobs_except"])), "release_source_comparison")
    checked_manifest(read_small(retained / "manifest.json"))
    require(identity(retained / "producer/producer-index.original.json")["sha256"] == ORIGIN_INDEX_SHA,
            "release_retained_index")
    asset = {key: terminal["asset"][key] for key in ("bytes", "sha256")}
    items = ((intake / "graph52-build-inputs.mwseal", asset),
             (intake / "graph52-build-inputs.original.tar", CAPSULE))
    require(terminal["capsule_path"] == str(items[1][0]), "release_exact_tar")
    observed = []
    for path, expected in items:
        guard(); require(identity(path, expected["bytes"], guard) == expected, "release_original_rehash")
        observed.append({"path": str(path), **expected, "physical": list(signature(path.lstat()))})
    intent = {"schema": "motionwright.graph.verified-transport-release/1", "current": current,
        "origin": ORIGIN, "retained_root": str(retained), "retained_members": MEMBERS,
        "source_comparison": comparison, "only_current_temporary_copies": observed,
        "host_original_or_draft_deleted": False, "negative_or_owner_original_deleted": False,
        "automatic_retry": False, "native_authority": False}
    new_json(intake / "release-intent.original.json", intent)
    released = []
    try:
        for (path, expected), before in zip(items, observed):
            guard()
            require(identity(path, expected["bytes"], guard) == expected
                    and list(namespace(path)) == before["physical"], "release_identity_changed")
            path.unlink(); released.append(path.name)
            require(not path.exists() and not path.is_symlink(), "release_physical_absence")
        if os.name != "nt":
            fd = os.open(intake, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        guard()
        value = {**intent, "status": "TWO_VERIFIED_TEMPORARY_COPIES_ABSENT",
                 "released": released, "retained_originals_preserved": True}
        return new_json(intake / "release-terminal.original.json", value)
    except BaseException as error:
        try:
            new_json(intake / "release-negative.original.json", {"status": "UNKNOWN_NO_RETRY",
                "error_type": type(error).__name__, "released_before_error": released,
                "automatic_retry": False, "native_authority": False})
        except BaseException:
            pass
        raise


def origin_member(index, ref):
    prefix = index["contract"]["execution"]["producer_root"] + "/"
    require(type(ref) is dict and set(ref) == {"path", "sha256", "size_bytes"}
            and ref["path"].startswith(prefix), "original_build_reference")
    return "producer/" + relative(ref["path"][len(prefix):])


def retained_reference(root, index, ref):
    path = Path(root) / origin_member(index, ref)
    require(identity(path, MAX_FILE) == {"bytes": ref["size_bytes"], "sha256": ref["sha256"]}, "original_reference_changed")
    return {**ref, "path": str(path)}


def expand_elf(source, destination, expected, *, guard=lambda: None):
    """Hash the exact zlib stream into a new single-link executable, in blocks."""
    destination = Path(destination); namespace(destination.parent, True)
    require(type(expected["size_bytes"]) is int and 0 < expected["size_bytes"] <= MAX_FILE, "ELF_bound")
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    unpack, count, hashed, header = zlib.decompressobj(), 0, hashlib.sha256(), bytearray()
    try:
        with stable_reader(source, MAX_FILE) as (incoming, _), os.fdopen(fd, "wb", closefd=False) as output:
            while block := incoming.read(CHUNK):
                guard(); pending = block
                while pending:
                    decoded = unpack.decompress(pending, CHUNK); pending = unpack.unconsumed_tail
                    count += len(decoded); require(count <= expected["size_bytes"], "expanded_ELF_bound")
                    if len(header) < 64: header.extend(decoded[:64 - len(header)])
                    require(output.write(decoded) == len(decoded), "ELF_short_write"); hashed.update(decoded)
                    require(not unpack.unused_data, "zlib_trailing_bytes")
            require(unpack.eof and not unpack.unused_data and not unpack.unconsumed_tail
                    and count == expected["size_bytes"] and hashed.hexdigest() == expected["sha256"], "ELF_original_digest")
            require(header[:7] == b"\x7fELF\x02\x01\x01" and header[18:20] == b"\x3e\x00", "ELF_linux_x86_64")
            output.flush(); os.fsync(fd)
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    actual = identity(destination, expected["size_bytes"], guard)
    require(actual == {"bytes": expected["size_bytes"], "sha256": expected["sha256"]}, "ELF_exclusive_copy_changed")
    return {"path": str(destination), "size_bytes": actual["bytes"], "sha256": actual["sha256"]}


def copy_builds(origin_root, index, source, comparison, producer, target_roots, *, guard=lambda: None):
    require(index["source"]["sha"] == ORIGIN["source_sha"] and index["source"]["tree"] == ORIGIN_TREE
            and index["source"]["sdk_sha"] == source["sdk_sha"] == ORIGIN["semwright_sha"]
            and index["contract"]["sdk_tree"] == SDK_TREE and set(index["builds"]) == set(ROLES), "original_build_scope")
    require(source["run_id"] not in ("42356210001", "42372165893") and source["run_attempt"] == 1
            and comparison["current_source"] == source["sha"] and comparison["current_tree"] == source["tree"],
            "current_copy_context")
    require(encoded(comparison) == encoded(source_comparison(
        comparison["origin_tree"], comparison["current_tree"], comparison["current_source"],
        comparison["unchanged_tracked_blobs_except"])), "material_comparison_changed")
    binaries, builds, contracts = {}, {}, {}
    for root in target_roots.values():
        Path(root).mkdir(mode=0o700, exist_ok=False)
        (Path(root) / "mw-r74-captured").mkdir(mode=0o700)
    for role in ROLES:
        original = index["builds"][role]
        blob = retained_reference(origin_root, index, original["retained_binary"])
        target = Path(target_roots["sdk" if role in SDK_ROLES else "product"]) / "mw-r74-captured" / (
            "semwright-sandbox" if role == "semwright_sandbox" else role)
        binaries[role] = expand_elf(Path(blob["path"]), target, index["binaries"][role], guard=guard)
        builds[role] = {"process": {name: retained_reference(origin_root, index, ref)
                                   for name, ref in original["process"].items()}, "retained_binary": blob}
        contracts[role] = index["contract"]["builds"][role]
    receipt = {"schema": SCHEMA, "origin": ORIGIN, "origin_tree": ORIGIN_TREE, "origin_sdk_tree": SDK_TREE,
        "origin_index": {"path": str(Path(origin_root) / "producer/producer-index.original.json"),
                         **{key: value for key, value in {"size_bytes": (Path(origin_root) / "producer/producer-index.original.json").stat().st_size,
                                                       "sha256": ORIGIN_INDEX_SHA}.items()}},
        "capsule": CAPSULE, "manifest_sha256": MANIFEST_SHA, "origin_root": str(origin_root),
        "source_comparison": comparison, "current": source, "roles": list(ROLES),
        "binaries": binaries, "retained_builds": builds,
        "new_builds": 0, "current_rust_kernel_test_execution_claimed": False,
        "historical_owner_credentials_or_tickets_reused": False, "native_authority": False}
    return binaries, builds, contracts, receipt


def validate_record(record, current_index, original):
    """Pure join for the live controller and independent bounded reader.

`original(ref, maximum)` must verify/read the actual retained bytes. This
validates the selected build subset only; it never treats the partial origin
capsule as a current producer index or adopts its runtime/owner authority.
"""
    require(set(record) == {"schema", "origin", "origin_tree", "origin_sdk_tree", "origin_index",
        "capsule", "manifest_sha256", "origin_root", "source_comparison", "current", "roles",
        "binaries", "retained_builds", "new_builds", "current_rust_kernel_test_execution_claimed",
        "historical_owner_credentials_or_tickets_reused", "native_authority", "abi_observation",
        "temporary_inputs_released", "source_comparison_processes"}, "reuse_record_fields")
    require(record["schema"] == SCHEMA and encoded(record["origin"]) == encoded(ORIGIN)
            and record["origin_tree"] == ORIGIN_TREE and record["origin_sdk_tree"] == SDK_TREE
            and record["capsule"] == CAPSULE and record["manifest_sha256"] == MANIFEST_SHA
            and record["roles"] == list(ROLES) and type(record["new_builds"]) is int and record["new_builds"] == 0
            and record["current_rust_kernel_test_execution_claimed"] is False
            and record["historical_owner_credentials_or_tickets_reused"] is False
            and record["native_authority"] is False, "reuse_scope_changed")
    current = current_index["source"]
    require(encoded(record["current"]) == encoded(current) and current["run_id"] not in ("42356210001", "42372165893")
            and type(current["run_attempt"]) is int and current["run_attempt"] == 1 and current["sdk_sha"] == ORIGIN["semwright_sha"]
            and current_index["contract"]["execution"].get("continuation", {}).get("selector") == SELECTOR,
            "reuse_current_continuation")
    proof = record["source_comparison"]
    require(encoded(proof) == encoded(source_comparison(ORIGIN_TREE, current["tree"], current["sha"],
                                                       proof["unchanged_tracked_blobs_except"])), "reuse_source_comparison")
    root = PurePosixPath(record["origin_root"])
    expected_root = PurePosixPath(current_index["contract"]["execution"]["producer_root"]) / "origin-build52"
    require(root == expected_root and root.is_absolute(), "reuse_origin_namespace")
    ref = record["origin_index"]
    require(ref["path"] == str(root / "producer/producer-index.original.json") and ref["sha256"] == ORIGIN_INDEX_SHA,
            "reuse_original_index_reference")
    raw = original(ref, MIB)
    require(sha(raw) == ORIGIN_INDEX_SHA, "reuse_original_index_digest")
    origin_index = strict(raw)
    require(origin_index["source"]["sha"] == ORIGIN["source_sha"] and origin_index["source"]["tree"] == ORIGIN_TREE
            and origin_index["source"]["run_id"] == ORIGIN["run_id"] and origin_index["source"]["run_attempt"] == 1
            and origin_index["source"]["sdk_sha"] == ORIGIN["semwright_sha"]
            and origin_index["contract"]["sdk_tree"] == SDK_TREE, "reuse_original_source")
    require(set(record["binaries"]) == set(record["retained_builds"]) == set(ROLES)
            and record["retained_builds"] == current_index["builds"]
            and current_index["contract"]["builds"] == origin_index["contract"]["builds"], "reuse_build_roster")
    for role in ROLES:
        old = origin_index["builds"][role]; new = record["retained_builds"][role]
        require(set(new) == {"process", "retained_binary"} and set(new["process"]) == {"command", "stdout", "stderr"},
                "reuse_process_roster")
        for before, after in [(old["retained_binary"], new["retained_binary"]),
                *((old["process"][key], new["process"][key]) for key in ("command", "stdout", "stderr"))]:
            require(after == {**before, "path": str(root / origin_member(origin_index, before))},
                    "reuse_original_reference_remap")
        binary, old_binary = record["binaries"][role], origin_index["binaries"][role]
        target = PurePosixPath(current["sdk_root"]) / "target" if role in SDK_ROLES else PurePosixPath(current["repository_root"]).parent / "v/t"
        require(binary == current_index["binaries"][role]
                and {key: binary[key] for key in ("sha256", "size_bytes")} == {key: old_binary[key] for key in ("sha256", "size_bytes")}
                and binary["path"] == str(target / "mw-r74-captured" / ("semwright-sandbox" if role == "semwright_sandbox" else role)),
                "reuse_actual_binary_copy")
    release = strict(original(record["temporary_inputs_released"], MAX_METADATA))
    require(release["status"] == "TWO_VERIFIED_TEMPORARY_COPIES_ABSENT"
            and release["retained_root"] == str(root) and release["retained_members"] == MEMBERS
            and release["source_comparison"] == proof and release["origin"] == ORIGIN
            and release["current"] == {"source_sha": current["sha"], "semwright_sha": current["sdk_sha"],
                                      "run_id": current["run_id"], "run_attempt": "1"}
            and release["released"] == ["graph52-build-inputs.mwseal", "graph52-build-inputs.original.tar"]
            and all(release[key] is False for key in ("host_original_or_draft_deleted",
                "negative_or_owner_original_deleted", "automatic_retry", "native_authority"))
            and release["retained_originals_preserved"] is True, "reuse_exact_temporary_release")
    abi = strict(original(record["abi_observation"], MAX_METADATA))
    require(set(abi) == {"schema", "current", "binaries", "interpreter", "loader", "loader_retained",
        "processes", "new_builds", "native_calls", "owner_authority"}
            and abi["schema"] == "motionwright.graph.reused-elf-current-loader-observation/1"
            and abi["current"] == current and abi["binaries"] == record["binaries"]
            and abi["interpreter"] == "/lib64/ld-linux-x86-64.so.2"
            and set(abi["processes"]) == set(ROLES) and type(abi["new_builds"]) is int and abi["new_builds"] == 0
            and type(abi["native_calls"]) is int and abi["native_calls"] == 0 and abi["owner_authority"] is False,
            "reuse_current_ABI_binding")
    require({key: abi["loader"][key] for key in ("size_bytes", "sha256")} ==
            {key: abi["loader_retained"][key] for key in ("size_bytes", "sha256")}, "reuse_loader_original")
    original(abi["loader_retained"], 4 * MIB)
    for role, refs in abi["processes"].items():
        require(set(refs) == {"command", "stdout", "stderr"}, "reuse_ABI_process_refs")
        command = strict(original(refs["command"], MIB))
        require(command["argv"] == [abi["loader"]["path"], "--list", record["binaries"][role]["path"]]
                and command["cwd"] == current["repository_root"] and command["exit_code"] == 0
                and command["process_boundary"] == "OWNED_POSIX_SESSION_GROUP"
                and command["owned_group_after_wait"] == "ABSENT" and command["escaped_descendants"] == "NOT_OBSERVED"
                and command["diagnostic_custody"] == "DIRECT_PROCESS_EXIT_OBSERVED" and command["cleanup_errors"] == []
                and not any(key in command for key in ("error", "observation", "uncertainty_reason", "receipt_error",
                                                       "secondary_observation_errors")), "reuse_ABI_process_closed")
        for name in ("stdout", "stderr"):
            data = original(refs[name], 8 * MIB)
            require(command[name] == {"bytes": len(data), "sha256": sha(data)}, "reuse_ABI_stream_binding")
            if name == "stdout":
                require(data and b"not found" not in data and b"ld-linux-x86-64.so.2" in data,
                        "reuse_ABI_loader_resolution")
    calls = [("rev-parse", "HEAD"), ("rev-parse", ORIGIN["source_sha"] + "^{tree}"),
             ("rev-parse", current["sha"] + "^{tree}"),
             ("diff", "--name-only", "--no-renames", "-z", ORIGIN["source_sha"], current["sha"]),
             ("diff", "--name-only", "HEAD", "--")]
    refs = record["source_comparison_processes"]
    require(type(refs) is list and len(refs) == len(calls), "reuse_git_original_roster")
    outputs = {}
    for args, entry in zip(calls, refs):
        require(set(entry) == {"command", "stdout", "stderr"}, "reuse_git_process_fields")
        command = strict(original(entry["command"], MIB))
        require(command["argv"] == ["git", "-C", current["repository_root"], *args]
                and command["cwd"] == current["repository_root"] and command["exit_code"] == 0
                and command["owned_group_after_wait"] == "ABSENT" and command["escaped_descendants"] == "NOT_OBSERVED"
                and command["diagnostic_custody"] == "DIRECT_PROCESS_EXIT_OBSERVED" and command["cleanup_errors"] == []
                and not any(key in command for key in ("error", "observation", "uncertainty_reason", "receipt_error",
                                                       "secondary_observation_errors")), "reuse_git_process_closed")
        for name in ("stdout", "stderr"):
            data = original(entry[name], MAX_METADATA)
            require(command[name] == {"bytes": len(data), "sha256": sha(data)}, "reuse_git_stream_binding")
            if name == "stdout": outputs[args] = data
    require(compare_git(current["sha"], lambda *args: outputs[args]) == proof,
            "reuse_git_material_originals")
    return origin_index


def origin_reader(record, original):
    """Map only the fixed capsule's selected original references, not an owner."""
    root = PurePosixPath(record["origin_root"])
    manifest = checked_manifest(original({"path": str(root / "manifest.json"),
        "sha256": MANIFEST_SHA, "size_bytes": MANIFEST_BYTES}, MAX_METADATA))
    members = {row["path"]: row for row in manifest["members"]}
    def retained(name, maximum=MAX_METADATA):
        require(name in members, "origin_selected_reference_missing")
        row = members[name]
        return original({"path": str(root / name), "sha256": row["sha256"], "size_bytes": row["bytes"]}, maximum)
    index = strict(retained("producer/producer-index.original.json"))
    request = strict(retained("producer/review-request.original.json"))
    require(request["source"] == index["source"] and request["index"]["sha256"] == ORIGIN_INDEX_SHA,
            "origin_request_index")
    aliases = {}
    for row in request["outside_original_copies"]:
        before, after = row["original"], row["retained"]
        require(before["path"] not in aliases and all(before[key] == after[key] for key in ("sha256", "size_bytes")),
                "origin_alias_changed")
        aliases[before["path"]] = after
    def reader(reference, maximum=MAX_METADATA):
        ref = aliases.get(reference["path"], reference)
        name = origin_member(index, ref)
        require(name in members and members[name]["sha256"] == reference["sha256"]
                and members[name]["bytes"] == reference["size_bytes"], "origin_selected_identity")
        return retained(name, maximum)
    return index, reader, retained


def validate_original_build_clocks(record, original):
    index, old_read, retained = origin_reader(record, original)
    execution = index["contract"]["execution"]
    deadline = strict(old_read(execution["deadline_original"], 8 * MIB + 65536))
    require(deadline["binding"]["GITHUB_SHA"] == ORIGIN["source_sha"]
            and deadline["binding"]["GITHUB_RUN_ID"] == ORIGIN["run_id"]
            and deadline["binding"]["GITHUB_RUN_ATTEMPT"] == "1" and deadline["job_seconds"] == 10800,
            "origin_original_job_clock")
    jobs = [row for row in deadline["jobs"]["jobs"] if row.get("id") == execution["job_id"]
            and row.get("name") == "Real SQLite and Studio / phase1-native-graph"]
    require(len(jobs) == 1, "origin_original_job")
    start = datetime.fromisoformat(jobs[0]["started_at"].replace("Z", "+00:00"))
    commands = [strict(old_read(index["builds"][role]["process"]["command"], MIB)) for role in ROLES]
    commands.append(strict(retained("producer/graph-store-file-bound-tests.command.json", MIB)))
    for command in commands:
        require(command["graph_job"] == {"source_sha": ORIGIN["source_sha"], "run_id": ORIGIN["run_id"],
            "run_attempt": 1, "job_id": execution["job_id"], "deadline_original": execution["deadline_original"]}
                and command["deadline_admission"]["deadline_mono_ns"] == deadline["deadline_mono_ns"],
                "origin_build_clock_not_current")
        begin, end = datetime.fromisoformat(command["started_utc"]), datetime.fromisoformat(command["finished_utc"])
        require(start <= begin <= end and (end - start).total_seconds() < 10800, "origin_build_time")
    return {"source_sha": ORIGIN["source_sha"], "run_id": ORIGIN["run_id"], "job_id": execution["job_id"],
            "eight_builds_observed_in_origin": True, "current_build_execution_claimed": False}
