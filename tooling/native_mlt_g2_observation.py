"""Read-only witnesses around the canonical single MLT assembly.

The caller owns the sole native dispatch. This module never starts a renderer,
installs a module, retries an operation, or grants media or human acceptance.
Its private receipts may contain owner paths and must not be published wholesale.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import zipfile

from cross_app_public_artifact import strict_object
from native_av_failure_guard import NativeCommandFailure
import native_mlt_loaded_module as loaded
import native_mlt_runtime_admission as admission

SCHEMA = "motionwright.native.mlt-g2-inputs/1"
TERMINAL_SCOPE = "canonical_assemble_command"
OFFICIAL_SCHEMA = "motionwright.mlt.official-runtime-intake/1"
REFERENCE_ZIP_SHA256 = "35ed8f9d2c840dd0d8702eaaf23454da87e49bdee2dd12f190b7be634806b27a"
REFERENCE_WAV_SHA256 = "66aeaa3aa6fff53834ab4003671ce79068bcbd6f631609f0f988b3ac9744ebe9"
REFERENCE_MANIFEST_SHA256 = "ca02511c2e7028a666d8c54f1494af096e19a47640d3cffe40865bfa7049f4b8"
REFERENCE_CHECKOUT = "a31505e3c0884a419b0b3b1072af0ba4057f9442"
REFERENCE_PR_HEAD = "30f02fd0b744cda36035a4473c98e066f89d3d00"
REPAIRED_MODULE_SHA256 = "c97df9c4b1d33dd6ea0ad5745501a70734f883709480622b680b8fcd512f7404"
COMPILER_RECEIPT_SHA256 = "0ec0d1a8fd000c3abd32f3b50d94fb20708b6d61327b216727b7a4551b202f75"
SEMWRIGHT_PIN = "54d3459a051adbce4cae9f60c0dea8f60c824be6"
MELT = Path("/usr/bin/melt-7")
MELT_ALIAS = Path("/usr/bin/melt")
MELT_ARCHIVE_ALIAS = {"archive_path": "usr/bin/melt",
                      "linkname": "melt-7", "target_path": "usr/bin/melt-7"}
INPUT_KEYS = {
    "schema", "motionwright_sha", "semwright_sha", "run_id", "run_attempt",
    "original_artifact_zip", "private_evidence_dir",
    "known_original_module_sha256", "known_melt_sha256",
    "official_runtime_receipt_path", "official_runtime_receipt_sha256",
    "original_c_source", "repaired_c_source", "compiled_module",
    "compiled_module_sha256", "compiler_receipt_path", "compiler_receipt_sha256",
    "installation_receipt_path", "installation_receipt_sha256",
}


class G2Unverified(AssertionError):
    """The native result is retained separately from a failed evidence gate."""


def require(ok: object, code: str) -> None:
    if not ok:
        raise G2Unverified(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_bytes(path: Path, data: bytes) -> None:
    """Exclusive private custody; a partial write is never a successful receipt."""
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    require(path.read_bytes() == data, "g2_private_custody_readback_changed")


def new_json(path: Path, value: object) -> None:
    new_bytes(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def warn_secondary() -> None:
    try:
        os.write(2, b"MLT G2 secondary evidence failure; native outcome retained.\n")
    except OSError:
        pass


def reference_source(raw: bytes) -> tuple[bytes, dict]:
    require(sha(raw) == REFERENCE_ZIP_SHA256, "g2_original_artifact_zip_changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        expected = {
            "native-master-source.wav", "native-multisegment-master.mp4",
            "native-two-segment-video-only.mkv", "public-evidence.json",
        }
        require(len(entries) == 4 and {item.filename for item in entries} == expected,
                "g2_original_artifact_members_changed")
        require(all(not item.is_dir() and not stat.S_ISLNK(item.external_attr >> 16)
                    and 0 < item.file_size <= 512_000 for item in entries)
                and sum(item.file_size for item in entries) <= 1_000_000,
                "g2_original_artifact_member_budget")
        require(archive.testzip() is None, "g2_original_artifact_crc")
        document = archive.read("public-evidence.json")
        require(sha(document) == REFERENCE_MANIFEST_SHA256,
                "g2_original_public_manifest_changed")
        proof = strict_object(document)
        require(proof.get("motionwright_source_sha") == REFERENCE_CHECKOUT
                and proof.get("semwright_source_pin") == SEMWRIGHT_PIN
                and proof.get("mode") == "final-mp4",
                "g2_original_public_manifest_identity")
        wav = archive.read("native-master-source.wav")
        require(len(wav) == 211_244 and sha(wav) == REFERENCE_WAV_SHA256,
                "g2_original_wav_changed")
    return wav, {
        "workflow_run_id": 39054798740, "run_attempt": 1,
        "workflow_event": "pull_request", "workflow_head_sha": REFERENCE_PR_HEAD,
        "producer_checkout_sha": REFERENCE_CHECKOUT, "artifact_id": 12109323512,
        "artifact_zip_sha256": REFERENCE_ZIP_SHA256,
        "public_manifest_sha256": REFERENCE_MANIFEST_SHA256,
        "original_wav_sha256": REFERENCE_WAV_SHA256,
        "source_is_synthetic_ci_fixture": True,
        "historical_right_channel_failure_superseded": False,
    }


def verify_official_runtime_bindings(official: dict, original_sha: str, melt_sha: str) -> None:
    """Match admitted receipt bytes; this does not independently authenticate them."""
    require(type(official) is dict and official.get("schema") == OFFICIAL_SCHEMA
            and official.get("runtime_bindings") == {
                "original_module": {"installed_path": admission.INSTALLED.as_posix(),
                                    "sha256": original_sha},
                "melt": {"installed_path": MELT.as_posix(), "sha256": melt_sha},
            }, "g2_official_runtime_binary_bindings")
    members = official.get("members")
    require(type(members) is dict and set(members) == {"libmlt7", "melt"},
            "g2_official_runtime_member_bindings")
    for name, path, digest in (("libmlt7", admission.INSTALLED, original_sha),
                               ("melt", MELT, melt_sha)):
        row = members[name]
        require(type(row) is dict and row.get("installed_path") == path.as_posix()
                and row.get("sha256") == digest and row.get("member_type") == "regular"
                and row.get("architecture_header") == "ELF64-LSB-x86_64",
                "g2_official_runtime_member_bindings")
    require(members["libmlt7"].get("archive_alias") is None
            and members["melt"].get("archive_alias") == MELT_ARCHIVE_ALIAS,
            "g2_official_runtime_alias_binding")


def _alias_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def verified_installed_melt_alias(expected_sha: str) -> dict:
    """Observe the one fixed alias without hashing or executing through it."""
    try:
        before = MELT_ALIAS.lstat()
        require(stat.S_ISLNK(before.st_mode), "g2_installed_melt_alias_not_symlink")
        target = os.readlink(MELT_ALIAS)
    except OSError as error:
        raise G2Unverified("g2_installed_melt_alias_unreadable") from error
    require(target == MELT_ARCHIVE_ALIAS["linkname"], "g2_installed_melt_alias_target")
    # Keep the existing regular-file, same-descriptor binary verifier strict.
    digest = loaded.sha256(MELT, expected_sha, loaded.MAX_MELT)
    try:
        after = MELT_ALIAS.lstat()
        target_after = os.readlink(MELT_ALIAS)
    except OSError as error:
        raise G2Unverified("g2_installed_melt_alias_changed") from error
    require(_alias_identity(before) == _alias_identity(after) and target_after == target,
            "g2_installed_melt_alias_changed")
    return {"installed_alias_path": MELT_ALIAS.as_posix(), "linkname": target,
            "canonical_installed_path": MELT.as_posix(), "sha256": digest,
            "alias_identity": list(_alias_identity(after))}



PROC_ROOT = Path("/proc")
MAX_OBSERVER_EVENTS = 64
MAX_OBSERVER_DIAGNOSTIC_BYTES = 2 * 1024 * 1024
PROC_DIAGNOSTIC_LIMITS = {
    "stat": 8192, "status": 16 * 1024, "uid_map": 4096, "gid_map": 4096,
    "cmdline": loaded.MAX_CMDLINE, "maps": 192 * 1024, "environ": loaded.MAX_ENV,
}


def bounded_error(error: BaseException) -> dict:
    """Private diagnostic only; never substitute it for the native terminal."""
    text = str(error).encode("utf-8", errors="replace")
    return {
        "exception_type": type(error).__name__,
        "message": text[:4096].decode("utf-8", errors="replace"),
        "message_truncated": len(text) > 4096,
        "errno": error.errno if isinstance(error, OSError) else None,
    }


def diagnostic_stat(path: Path) -> dict:
    try:
        value = path.stat()
        return {"path": str(path), "device": value.st_dev, "inode": value.st_ino,
                "mode": value.st_mode, "uid": value.st_uid, "gid": value.st_gid}
    except OSError as error:
        return {"path": str(path), "error": bounded_error(error)}


def diagnostic_read(path: Path, budget: int) -> dict:
    try:
        with path.open("rb") as stream:
            raw = stream.read(budget + 1)
        retained = raw[:budget]
        return {"path": str(path), "encoding": "base64",
                "raw": base64.b64encode(retained).decode("ascii"),
                "retained_bytes": len(retained), "retained_sha256": sha(retained),
                "complete_read": len(raw) <= budget}
    except OSError as error:
        return {"path": str(path), "error": bounded_error(error), "complete_read": False}


def diagnostic_link(path: Path) -> dict:
    try:
        target = os.readlink(path)
        return {"path": str(path), "target": target[:4096],
                "target_truncated": len(target) > 4096}
    except OSError as error:
        return {"path": str(path), "error": bounded_error(error)}


def diagnostic_process(pid: int, *, include_native_bytes: bool = False) -> dict:
    """Separately sampled proc reads are diagnostic, never a module attestation."""
    proc = PROC_ROOT / str(pid)
    names = ("stat", "status", "uid_map", "gid_map")
    if include_native_bytes:
        names += ("cmdline", "maps", "environ")
    return {
        "pid": pid, "proc": diagnostic_stat(proc),
        "root": diagnostic_stat(proc / "root"),
        "links": {name: diagnostic_link(proc / name)
                  for name in ("exe", "root", "cwd", "ns/mnt", "ns/user", "ns/pid")},
        "raw_reads": {name: diagnostic_read(proc / name, PROC_DIAGNOSTIC_LIMITS[name])
                      for name in names},
        "sample_scope": "separate-read-only-observations-not-attestation",
    }


def process_identity(pid: int) -> tuple[int, int]:
    with (PROC_ROOT / str(pid) / "stat").open("rb") as stream:
        raw = stream.read(8193)
    start = loaded.parse_process_starttime(raw)
    tail = raw.decode("ascii").rsplit(")", 1)[1].split()
    return int(tail[1]), start


def descendant_of(pid: int, ancestor: tuple[int, int]) -> bool:
    visited = set()
    for _ in range(64):
        if pid in visited or pid <= 1:
            return False
        visited.add(pid)
        parent, start = process_identity(pid)
        if pid == ancestor[0]:
            return start == ancestor[1]
        pid = parent
    return False


def selected_consumer(payload: bytes, output_root: Path) -> Path | None:
    if not (0 < len(payload) <= loaded.MAX_CMDLINE and payload.endswith(b"\0")):
        return None
    argv = payload[:-1].decode("utf-8").split("\0")
    consumers = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "-consumer"]
    if len(consumers) != 1 or not consumers[0].startswith("avformat:"):
        return None
    output = Path(consumers[0][len("avformat:"):])
    if output.suffix != ".mp4" or not output.is_absolute():
        return None
    if output_root not in output.parents:
        return None
    require(output.resolve() == output and ".." not in output.parts
            and not any(parent.is_symlink() for parent in output.parents),
            "g2_consumer_output_not_canonical")
    loaded.verified_melt_cmdline(payload, str(output))
    return output



HOST_MELT = "/plugin/tools/melt"
HOST_RUNNER_ARGUMENTS = [
    "/plugin/bin", "av-operation", "--runtime-root", "/workspace/mlt-runtime",
    "--melt-sealed", HOST_MELT, "--ffprobe-sealed", "/plugin/tools/ffprobe",
    "--ffmpeg-sealed", "/plugin/tools/ffmpeg",
    "--project-root", "/workspace/project", "--media-root", "/workspace/media",
    "--output-root", "/workspace/output",
]
MAX_NATIVE_OUTPUT = 256_000_000
PRIVATE_WORK = re.compile(r"/tmp/semwright-video-[0-9a-f]{32}\Z")


def node_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid)


def file_generation(value: os.stat_result) -> tuple[int, ...]:
    return node_identity(value) + (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def bounded_proc_bytes(path: Path, budget: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(budget + 1)
    require(0 < len(raw) <= budget, "g2_proc_input_budget")
    return raw


def argv_bytes(raw: bytes) -> list[str]:
    require(0 < len(raw) <= loaded.MAX_CMDLINE and raw.endswith(b"\0"), "g2_proc_argv_budget")
    return [value.decode("utf-8") for value in raw[:-1].split(b"\0")]


def host_tool_consumer(raw: bytes, cwd: str) -> str | None:
    argv = argv_bytes(raw)
    consumers = [argv[i + 1] for i, value in enumerate(argv[:-1]) if value == "-consumer"]
    if not consumers or not any(value.endswith(".mp4") for value in consumers):
        return None
    require(len(consumers) == 1, "g2_host_consumer_ambiguous")
    selected = consumers[0]
    if selected.startswith("avformat:/"):
        output = selected[len("avformat:"):]
        logical_cwd, separator, basename = output.rpartition("/")
        require(separator and basename == "partial.mp4"
                and PRIVATE_WORK.fullmatch(logical_cwd) is not None,
                "g2_host_consumer_cwd_is_not_private_work")
    else:
        require(PRIVATE_WORK.fullmatch(cwd) is not None,
                "g2_host_consumer_cwd_is_not_private_work")
        logical_cwd, output = cwd, cwd + "/partial.mp4"
    require(argv[0] == HOST_MELT, "g2_host_consumer_executable_argument")
    loaded.verified_melt_cmdline(raw, output, relative_cwd=logical_cwd)
    return output


def host_tool_contract(output_root: Path) -> dict:
    owner = output_root.parent
    require(owner.resolve(strict=True) == owner and not owner.is_symlink()
            and output_root.name == "output" and not output_root.is_symlink(),
            "g2_host_owner_namespace_root")
    owner_info, output_info = owner.lstat(), output_root.lstat()
    require(stat.S_ISDIR(owner_info.st_mode) and stat.S_ISDIR(output_info.st_mode)
            and owner_info.st_uid == output_info.st_uid == os.getuid()
            and stat.S_IMODE(owner_info.st_mode) == stat.S_IMODE(output_info.st_mode) == 0o700,
            "g2_host_owner_namespace_mode")
    manifest_path = owner / "config" / "mlt-video-driver.json"
    manifest_raw = admission.checked_bytes(manifest_path, 64 * 1024)
    manifest = strict_object(manifest_raw)
    require(manifest.get("id") == "mlt-video" and type(manifest.get("tools")) is list,
            "g2_host_runner_manifest")
    entries = [item for item in manifest["tools"]
               if type(item) is dict and item.get("name") == "mlt-runner"]
    require(len(entries) == 1 and entries[0].get("root") == "mlt-runner-root"
            and entries[0].get("dependencies") == ["melt", "ffprobe", "ffmpeg"],
            "g2_host_runner_manifest")
    runner = owner / "bin" / "semwright-mlt-runtime-runner"
    runner_raw = admission.checked_bytes(runner, loaded.MAX_MELT)
    digest = entries[0].get("sha256")
    require(type(digest) is str and loaded.HEX.fullmatch(digest)
            and sha(runner_raw) == digest, "g2_host_runner_source_pin")
    return {"owner_path": str(owner), "owner_identity": list(node_identity(owner_info)),
            "output_path": str(output_root), "output_identity": list(node_identity(output_info)),
            "manifest_path": str(manifest_path), "manifest_sha256": sha(manifest_raw),
            "runner_path": str(runner), "runner_sha256": digest}


def namespace_path(proc: Path, absolute: str, *, directory: bool = False) -> int:
    """Open fixed namespace components below this PID's kernel-owned root link."""
    require(absolute.startswith("/") and not absolute.startswith("//")
            and "\\" not in absolute and "\0" not in absolute,
            "g2_namespace_absolute_path")
    parts = absolute[1:].split("/")
    require(0 < len(parts) <= 8 and all(part not in {"", ".", ".."} for part in parts),
            "g2_namespace_components")
    base_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
                  | getattr(os, "O_BINARY", 0))
    parent = os.open(proc / "root", base_flags | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, base_flags | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        flags = base_flags | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0)
        return os.open(parts[-1], flags, dir_fd=parent)
    finally:
        os.close(parent)


def namespace_directory(proc: Path, absolute: str) -> os.stat_result:
    fd = namespace_path(proc, absolute, directory=True)
    try:
        value = os.fstat(fd)
        require(stat.S_ISDIR(value.st_mode), "g2_namespace_not_directory")
        return value
    finally:
        os.close(fd)


def matching_write_descriptors(proc: Path, expected: os.stat_result) -> list[int]:
    entries = list((proc / "fd").iterdir())
    require(len(entries) <= 512, "g2_native_descriptor_inventory_budget")
    matches = []
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            actual = entry.stat()
            if node_identity(actual) != node_identity(expected):
                continue
            raw = bounded_proc_bytes(proc / "fdinfo" / entry.name, 8192).decode("ascii")
            flags = [line.split(":", 1)[1].strip() for line in raw.splitlines()
                     if line.startswith("flags:")]
            require(len(flags) == 1 and re.fullmatch(r"[0-7]+", flags[0]),
                    "g2_native_output_fd_flags")
            if int(flags[0], 8) & os.O_ACCMODE in (os.O_WRONLY, os.O_RDWR):
                matches.append(int(entry.name))
        except FileNotFoundError:
            # A closing unrelated descriptor is not a witness.
            continue
    require(matches, "g2_original_melt_open_output_not_observed")
    return sorted(matches)


def read_held_output(fd: int, opened: os.stat_result) -> tuple[bytes, dict]:
    before = os.fstat(fd)
    require(node_identity(before) == node_identity(opened)
            and stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_NATIVE_OUTPUT,
            "g2_held_output_identity_or_budget")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks, total = [], 0
    while True:
        block = os.read(fd, min(1024 * 1024, MAX_NATIVE_OUTPUT - total + 1))
        if not block:
            break
        total += len(block)
        require(total <= MAX_NATIVE_OUTPUT, "g2_held_output_budget")
        chunks.append(block)
    after = os.fstat(fd)
    require(file_generation(before) == file_generation(after) and total == before.st_size,
            "g2_held_output_changed_during_read")
    raw = b"".join(chunks)
    return raw, {"sha256": sha(raw), "bytes": len(raw),
                 "file_identity": list(node_identity(after)),
                 "links_at_read_time": after.st_nlink,
                 "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns,
                 "scope": "same-open-descriptor-stable-at-read-time"}


class NamespaceMaster:
    """Retain only the file actually opened for writing by the attested melt PID."""
    def __init__(self, identity: tuple[int, int], fd: int,
                 opened: os.stat_result, receipt: dict):
        self.identity, self.fd, self.opened, self.receipt = identity, fd, opened, receipt
        self.private_snapshot: dict | None = None
        self.snapshot_attempted = False
        self.closed = False

    @classmethod
    def observe(cls, pid: int, ancestor: tuple[int, int], output_root: Path,
                contract: dict, melt_sha: str):
        proc = PROC_ROOT / str(pid)
        parent_pid, start = process_identity(pid)
        require(proc.stat().st_uid == os.getuid() and descendant_of(pid, ancestor),
                "g2_host_native_ancestry_or_uid")
        cwd = os.readlink(proc / "cwd")
        raw = bounded_proc_bytes(proc / "cmdline", loaded.MAX_CMDLINE)
        output = host_tool_consumer(raw, cwd)
        if output is None:
            return None
        observed_cwd, cwd = cwd, output.rpartition("/")[0]
        require(contract is not None and contract == host_tool_contract(output_root),
                "g2_host_source_contract_changed")
        parent = PROC_ROOT / str(parent_pid)
        parent_start = process_identity(parent_pid)[1]
        require(parent.stat().st_uid == os.getuid()
                and descendant_of(parent_pid, ancestor)
                and argv_bytes(bounded_proc_bytes(parent / "cmdline", loaded.MAX_CMDLINE))
                    == HOST_RUNNER_ARGUMENTS,
                "g2_host_runtime_parent_not_original")
        namespace = {name: os.readlink(proc / "ns" / name) for name in ("mnt", "user", "pid")}
        require(all(os.readlink(parent / "ns" / name) == value for name, value in namespace.items())
                and namespace["mnt"] != os.readlink(PROC_ROOT / "self" / "ns" / "mnt"),
                "g2_host_runtime_namespace_mismatch")
        root_identity = node_identity((proc / "root").stat())
        require(root_identity == node_identity((parent / "root").stat()),
                "g2_host_runtime_root_mismatch")
        mapped_output = namespace_directory(proc, "/workspace/output")
        require(node_identity(mapped_output) == tuple(contract["output_identity"])
                == node_identity(output_root.lstat()),
                "g2_host_output_mount_not_original_owner")
        work = namespace_directory(proc, cwd)
        require(work.st_uid == os.getuid() and stat.S_IMODE(work.st_mode) == 0o700
                and node_identity(work) == node_identity((proc / "cwd").stat()),
                "g2_host_private_work_identity")
        fd = namespace_path(proc, output)
        try:
            opened = os.fstat(fd)
            require(stat.S_ISREG(opened.st_mode) and opened.st_uid == os.getuid()
                    and not stat.S_IMODE(opened.st_mode) & 0o022
                    and opened.st_nlink == 1 and 0 <= opened.st_size <= MAX_NATIVE_OUTPUT,
                    "g2_host_native_output_not_private_regular")
            writers = matching_write_descriptors(proc, opened)
            runner_path = parent / "root" / "plugin" / "bin"
            require((parent / "root" / "plugin").is_dir()
                    and not (parent / "root" / "plugin").is_symlink(),
                    "g2_host_runtime_namespace_directory")
            with loaded._verified_binary(runner_path, contract["runner_sha256"],
                                         loaded.MAX_MELT) as runner:
                require(node_identity((parent / "exe").stat()) == node_identity(runner["stat"])
                        and runner["stat"].st_uid == os.getuid()
                        and not stat.S_IMODE(runner["stat"].st_mode) & 0o022,
                        "g2_host_runtime_parent_executable")
                proof = loaded.inspect_live_original(
                    pid, MELT, melt_sha, admission.INSTALLED, REPAIRED_MODULE_SHA256,
                    output, host_sealed_melt=True,
                )
                require(process_identity(pid) == (parent_pid, start)
                        and process_identity(parent_pid)[1] == parent_start
                        and descendant_of(pid, ancestor)
                        and node_identity((parent / "exe").stat()) == node_identity(runner["stat"])
                        and node_identity((proc / "root").stat()) == root_identity
                        and node_identity((proc / "cwd").stat()) == node_identity(work)
                        and all(os.readlink(proc / "ns" / name) == value
                                for name, value in namespace.items()),
                        "g2_host_process_context_changed")
                require(matching_write_descriptors(proc, opened),
                        "g2_host_native_output_writer_changed")
                receipt = {
                    "pid": pid, "process_start_ticks": start,
                    "daemon_pid": ancestor[0], "daemon_start_ticks": ancestor[1],
                    "output_path": output, "live_module": proof,
                    "runtime_parent": {"pid": parent_pid, "process_start_ticks": parent_start,
                                       "sha256": contract["runner_sha256"],
                                       "file_identity": list(node_identity(runner["stat"]))},
                    "namespace": namespace, "namespace_root_identity": list(root_identity),
                    "private_cwd": cwd, "observed_cwd_link": observed_cwd,
                    "private_cwd_identity": list(node_identity(work)),
                    "owner_output_identity": list(node_identity(mapped_output)),
                    "original_output_file_identity": list(node_identity(opened)),
                    "original_melt_write_descriptors": writers,
                    "owner_contract": contract,
                    "publication_binding": "PENDING_ORIGINAL_SOURCE_BYTES",
                }
            return cls((pid, start), fd, opened, receipt)
        except BaseException:
            os.close(fd)
            raise

    def retain(self, evidence: Path) -> None:
        if self.snapshot_attempted:
            return
        self.snapshot_attempted = True
        raw, snapshot = read_held_output(self.fd, self.opened)
        name = f"original-melt-consumer-{self.identity[0]}-{self.identity[1]}"
        new_bytes(evidence / (name + ".mp4"), raw)
        new_json(evidence / (name + ".json"), {
            **snapshot, "process_identity": list(self.identity),
            "namespace_output_path": self.receipt["output_path"],
            "native_terminal_inferred": False, "native_operation_retried": False,
        })
        self.private_snapshot = snapshot

    def bind_master(self, master: Path) -> dict:
        require(self.private_snapshot is not None, "g2_original_consumer_output_not_retained")
        try:
            still_original = process_identity(self.identity[0])[1] == self.identity[1]
        except FileNotFoundError:
            still_original = False
        require(not still_original, "g2_original_melt_still_live_after_result")
        raw, snapshot = read_held_output(self.fd, self.opened)
        published = admission.checked_bytes(master, MAX_NATIVE_OUTPUT)
        require(snapshot["sha256"] == self.private_snapshot["sha256"]
                and snapshot["bytes"] == self.private_snapshot["bytes"]
                and raw == published, "g2_original_consumer_bytes_differ_from_published_master")
        output_root = Path(self.receipt["owner_contract"]["output_path"])
        require(output_root in master.resolve(strict=True).parents
                and self.receipt["owner_contract"] == host_tool_contract(output_root),
                "g2_host_source_contract_changed_after_native_result")
        return {
            **self.receipt, "output_path": str(master.resolve(strict=True)),
            "namespace_output_path": self.receipt["output_path"],
            "publication_binding": "SAME_ORIGINAL_OPEN_DESCRIPTOR_BYTES",
            "published_output_sha256": sha(published), "published_output_bytes": len(published),
            "source_descriptor_readback": snapshot,
            "publication_source_pin": SEMWRIGHT_PIN,
            "physical_AV_master_acceptance": False, "human_acceptance": False,
        }

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.fd)



class LiveObserver:
    """Observe only; a missed or ambiguous original PID cannot cause a replay."""
    def __init__(self, daemon_pid: int, output_root: Path, melt_sha: str):
        self.ancestor = (daemon_pid, process_identity(daemon_pid)[1])
        self.output_root = output_root.resolve(strict=True)
        self.melt_sha = melt_sha
        self.candidates: set[tuple[int, int]] = set()
        self.receipts: dict[tuple[int, int], dict] = {}
        self.outputs: dict[tuple[int, int], str] = {}
        self.observation_failures = 0
        self.fatal = False
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.scans = 0
        self.first_scan_pids: list[int] = []
        self.last_scan_pids: list[int] = []
        self.filter_counts: dict[str, int] = {}
        self.events: list[dict] = []
        self.event_keys: dict[tuple, int] = {}
        self.event_bytes = 0
        self.dropped_events = 0
        self.namespace_masters: dict[tuple[int, int], NamespaceMaster] = {}
        self.host_contract: dict | None = None
        self.host_contract_error: dict | None = None
        try:
            self.host_contract = host_tool_contract(self.output_root)
        except Exception as error:
            self.host_contract_error = bounded_error(error)
        self.scope = {
            "observer_pid": os.getpid(), "observer_uid": os.getuid(),
            "observer_euid": os.geteuid(), "proc_root": diagnostic_stat(PROC_ROOT),
            "output_root": diagnostic_stat(self.output_root),
            "observer": diagnostic_process(os.getpid()),
            "daemon": diagnostic_process(daemon_pid),
        }

    def counted(self, stage: str) -> None:
        with self.lock:
            self.filter_counts[stage] = self.filter_counts.get(stage, 0) + 1

    def record(self, stage: str, *, pid: int | None = None,
               error: BaseException | None = None, native: bool = False,
               detail: dict | None = None) -> None:
        failure = None if error is None else bounded_error(error)
        key = (pid, stage, None if failure is None else json.dumps(failure, sort_keys=True))
        with self.lock:
            previous = self.event_keys.get(key)
            if previous is not None:
                self.events[previous]["observations"] += 1
                self.events[previous]["last_scan"] = self.scans
                return
            if len(self.events) >= MAX_OBSERVER_EVENTS:
                self.dropped_events += 1
                return
        entry = {"stage": stage, "first_scan": self.scans, "last_scan": self.scans,
                 "observations": 1, "error": failure, "detail": detail,
                 "process": None if pid is None else diagnostic_process(
                     pid, include_native_bytes=native)}
        size = len(json.dumps(entry, sort_keys=True).encode())
        with self.lock:
            if self.event_bytes + size > MAX_OBSERVER_DIAGNOSTIC_BYTES:
                self.dropped_events += 1
                return
            self.event_keys[key] = len(self.events)
            self.events.append(entry)
            self.event_bytes += size

    def is_melt_name(self, proc: Path, executable: str | None = None) -> bool:
        if executable is not None and Path(executable).name in {"melt", "melt-7"}:
            return True
        try:
            with (proc / "comm").open("rb") as stream:
                return stream.read(64).strip() in {b"melt", b"melt-7"}
        except OSError:
            return False

    def scan(self) -> None:
        pids = sorted(int(path.name) for path in PROC_ROOT.iterdir() if path.name.isdecimal())
        require(len(pids) <= 8192, "g2_process_inventory_budget")
        with self.lock:
            self.scans += 1
            if self.scans == 1:
                self.first_scan_pids = pids
            self.last_scan_pids = pids
        canonical_melt = str(MELT.resolve(strict=True))
        for pid in pids:
            proc = PROC_ROOT / str(pid)
            stage = "executable_read"
            native = False
            try:
                try:
                    executable = os.readlink(proc / "exe")
                except OSError as error:
                    self.counted("executable_unreadable")
                    if self.is_melt_name(proc):
                        self.record("executable_unreadable", pid=pid, error=error)
                    continue
                if executable != canonical_melt:
                    self.counted("executable_mismatch")
                    if self.is_melt_name(proc, executable):
                        self.record("executable_mismatch", pid=pid,
                                    detail={"observed_executable": executable,
                                            "expected_executable": canonical_melt})
                        stage = "host_sealed_consumer"
                        # Name is discovery only. Namespace, parent, source hashes,
                        # mappings and the original write descriptor provide authority.
                        self.observe_namespace(pid)
                    continue
                stage = "process_uid"
                if proc.stat().st_uid != os.getuid():
                    self.counted("uid_mismatch")
                    self.record("uid_mismatch", pid=pid,
                                detail={"required_uid": os.getuid()})
                    continue
                stage = "daemon_ancestry"
                if not descendant_of(pid, self.ancestor):
                    self.counted("not_daemon_descendant")
                    self.record("not_daemon_descendant", pid=pid)
                    continue
                native = True
                stage = "consumer_selection"
                before = process_identity(pid)[1]
                with (proc / "cmdline").open("rb") as stream:
                    output = selected_consumer(stream.read(loaded.MAX_CMDLINE + 1), self.output_root)
                if output is None:
                    self.counted("consumer_unselected")
                    self.record("consumer_unselected", pid=pid, native=True)
                    continue
                identity = (pid, before)
                with self.lock:
                    self.candidates.add(identity)
                    self.outputs[identity] = str(output)
                    count = len(self.candidates)
                    witnessed = identity in self.receipts
                require(count <= 1, "g2_multiple_original_av_consumers")
                if witnessed:
                    self.counted("witness_already_retained")
                    continue
                stage = "loaded_module_attestation"
                proof = loaded.inspect_live_original(
                    pid, MELT, self.melt_sha, admission.INSTALLED,
                    REPAIRED_MODULE_SHA256, str(output),
                )
                require(process_identity(pid)[1] == before
                        and descendant_of(pid, self.ancestor),
                        "g2_native_process_identity_changed")
                with self.lock:
                    self.receipts[identity] = {
                        "pid": pid, "process_start_ticks": before,
                        "daemon_pid": self.ancestor[0],
                        "daemon_start_ticks": self.ancestor[1],
                        "output_path": str(output), "live_module": proof,
                    }
                self.counted("module_witness_retained")
                self.record("module_witness_retained", pid=pid, native=True,
                            detail={"process_start_ticks": before, "output_path": str(output)})
            except G2Unverified as error:
                self.fatal = True
                self.observation_failures += 1
                self.counted(stage + "_fatal")
                self.record(stage, pid=pid, error=error, native=native)
            except (OSError, ValueError, loaded.LoadedModuleUnverified) as error:
                # Another read of this live PID never replays a native effect.
                self.observation_failures += 1
                self.counted(stage + "_error")
                self.record(stage, pid=pid, error=error, native=native)

    def observe_namespace(self, pid: int) -> None:
        proc = PROC_ROOT / str(pid)
        if proc.stat().st_uid != os.getuid() or not descendant_of(pid, self.ancestor):
            self.counted("host_consumer_uid_or_ancestry_rejected")
            return
        identity = (pid, process_identity(pid)[1])
        if identity in self.namespace_masters:
            return
        require(len(self.namespace_masters) < 4, "g2_host_candidate_descriptor_budget")
        captured = NamespaceMaster.observe(
            pid, self.ancestor, self.output_root, self.host_contract, self.melt_sha,
        )
        if captured is None:
            self.counted("host_consumer_unselected")
            return
        if captured.identity != identity:
            captured.close()
            raise G2Unverified("g2_host_process_identity_changed_before_capture")
        with self.lock:
            self.namespace_masters[identity] = captured
            self.candidates.add(identity)
            self.outputs[identity] = captured.receipt["output_path"]
            self.receipts[identity] = captured.receipt
            count = len(self.candidates)
        require(count <= 1, "g2_multiple_original_av_consumers")
        self.counted("host_consumer_witness_retained")
        self.record("host_consumer_witness_retained", pid=pid, native=True,
                    detail={"process_start_ticks": identity[1],
                            "namespace_output_path": captured.receipt["output_path"]})

    def retain_intermediate(self, evidence_dir: Path) -> None:
        failures = []
        for identity, captured in self.namespace_masters.items():
            try:
                captured.retain(evidence_dir)
            except Exception as error:
                failures.append(identity)
                self.record("original_consumer_snapshot", pid=identity[0], error=error)
        require(not failures, "g2_original_consumer_snapshot_failed")

    def close(self) -> None:
        for captured in self.namespace_masters.values():
            captured.close()

    def start(self) -> None:
        require(self.thread is None, "g2_observer_already_started")
        try:
            require(self.host_contract is not None, "g2_host_contract_not_preflighted")
            self.scan()
            require(not self.candidates and not self.fatal,
                    "g2_consumer_preexists_original_assemble")
        except Exception as error:
            self.fatal = True
            self.record("observer_start", error=error)
            raise

        def watch() -> None:
            try:
                while not self.stop_event.is_set():
                    self.scan()
                    self.stop_event.wait(0.02)
            except Exception as error:
                self.fatal = True
                self.observation_failures += 1
                self.record("observer_watch", error=error)

        self.thread = threading.Thread(target=watch, name="mlt-original-observer", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
            require(not self.thread.is_alive(), "g2_observer_did_not_finish")

    def diagnostic(self) -> dict:
        alive = self.thread is not None and self.thread.is_alive()
        with self.lock:
            result = {
                "schema": "motionwright.native.mlt-live-observer-diagnostic/1",
                "scope": self.scope, "daemon_identity": list(self.ancestor),
                "host_contract": self.host_contract,
                "host_contract_error": self.host_contract_error,
                "scans": self.scans, "first_scan_pids": self.first_scan_pids,
                "last_scan_pids": self.last_scan_pids, "filter_counts": self.filter_counts,
                "candidates": [list(identity) for identity in sorted(self.candidates)],
                "outputs": [{"identity": list(key), "output": value}
                            for key, value in sorted(self.outputs.items())],
                "receipts": [{"identity": list(key), "receipt": value}
                             for key, value in sorted(self.receipts.items())],
                "observation_failures": self.observation_failures, "fatal": self.fatal,
                "events": self.events, "dropped_events": self.dropped_events,
                "event_byte_budget": MAX_OBSERVER_DIAGNOSTIC_BYTES,
                "observer_thread_alive_at_snapshot": alive,
                "bounded_journal_complete": not alive and self.dropped_events == 0,
                "all_proc_reads_claimed": False,
                "native_operation_retried": False, "module_attestation_inferred": False,
            }
            # Detach the retained snapshot if a failed stop leaves the watcher live.
            return json.loads(json.dumps(result))

    def require_one(self, master: Path) -> dict:
        require(not self.fatal and len(self.candidates) == 1 and len(self.receipts) == 1,
                "g2_original_live_module_not_uniquely_witnessed")
        identity, proof = next(iter(self.receipts.items()))
        if identity in self.namespace_masters:
            return self.namespace_masters[identity].bind_master(master)
        require(proof["output_path"] == str(master.resolve(strict=True)),
                "g2_observed_consumer_is_not_native_result_artifact")
        return proof


class G2Session:
    def __init__(self, inputs: dict, evidence_dir: Path, wav: bytes, provenance: dict):
        self.inputs = inputs
        self.evidence_dir = evidence_dir
        self.original_wav = wav
        self.provenance = provenance
        self.native_result: dict | None = None
        self.native_error: BaseException | None = None
        self.native_traceback = None
        self.secondary_failures: list[str] = []
        self.observer: LiveObserver | None = None
        self.live_proof: dict | None = None
        self.pcm_proof: dict | None = None
        self.master_sha: str | None = None
        self.packet_observation: dict | None = None
        self.completed = False
        self.assembled_once = False
        self.melt_alias_observation: dict | None = None
        self.observer_diagnostic_attempted = False
        self.observer_diagnostic_sha256: str | None = None
        self.observer_error_details: dict[str, dict] = {}

    @classmethod
    def prepare(cls, path: Path, *, source_sha: str, semwright_sha: str, mode: str):
        require(mode == "final-mp4", "g2_requires_single_final_mp4_mode")
        runner = admission.disposable_linux_runner()
        data = strict_object(admission.checked_bytes(path, 64 * 1024))
        require(set(data) == INPUT_KEYS and data["schema"] == SCHEMA, "g2_inputs_schema")
        require(re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None
                and data["motionwright_sha"] == source_sha
                and data["semwright_sha"] == semwright_sha == SEMWRIGHT_PIN
                and data["run_id"] == os.environ.get("GITHUB_RUN_ID")
                and data["run_attempt"] == os.environ.get("GITHUB_RUN_ATTEMPT") == "1"
                and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
                and type(data["run_id"]) is str and data["run_id"].isdecimal()
                and type(data["run_attempt"]) is str and data["run_attempt"].isdecimal(),
                "g2_current_execution_binding")
        for key in INPUT_KEYS:
            if key.endswith("_sha256"):
                require(type(data[key]) is str and re.fullmatch(r"[0-9a-f]{64}", data[key]),
                        "g2_required_digest_invalid")
        require(data["compiled_module_sha256"] == REPAIRED_MODULE_SHA256
                and data["compiler_receipt_sha256"] == COMPILER_RECEIPT_SHA256,
                "g2_repair_only_original_G1_binary_changed")
        official_bytes = admission.checked_bytes(Path(data["official_runtime_receipt_path"]), 512 * 1024)
        require(sha(official_bytes) == data["official_runtime_receipt_sha256"],
                "g2_official_runtime_receipt_changed")
        official = strict_object(official_bytes)
        verify_official_runtime_bindings(
            official, data["known_original_module_sha256"], data["known_melt_sha256"])
        closure = admission.source_compiler_closure(
            Path(data["original_c_source"]), Path(data["repaired_c_source"]),
            Path(data["compiled_module"]), data["compiled_module_sha256"],
            Path(data["compiler_receipt_path"]), data["compiler_receipt_sha256"],
        )
        installed_bytes = admission.checked_bytes(Path(data["installation_receipt_path"]), 64 * 1024)
        require(sha(installed_bytes) == data["installation_receipt_sha256"],
                "g2_installation_receipt_changed")
        installed = strict_object(installed_bytes)
        require(installed.get("schema") == "motionwright.native.mlt-module-installed/1"
                and installed.get("installed_path") == str(admission.INSTALLED)
                and installed.get("original_module_sha256") == data["known_original_module_sha256"]
                and installed.get("original_backup_sha256") == data["known_original_module_sha256"]
                and installed.get("actual_installed_module_sha256") == REPAIRED_MODULE_SHA256
                and installed.get("module_replaced") is True
                and installed.get("new_native_master_issued") is False
                and all(installed.get(key) == value for key, value in closure.items()),
                "g2_installation_source_closure")
        require(sha(admission.checked_bytes(admission.INSTALLED, admission.MAX_ELF_BYTES))
                == REPAIRED_MODULE_SHA256, "g2_installed_repaired_module_changed")
        alias_observation = verified_installed_melt_alias(data["known_melt_sha256"])
        require(not admission.live_melt_pids(), "g2_melt_exists_before_original_assembly")
        wav, provenance = reference_source(
            admission.checked_bytes(Path(data["original_artifact_zip"]), 32 * 1024 * 1024)
        )
        private = Path(data["private_evidence_dir"])
        require(private.is_absolute() and private.parent == runner
                and not private.exists() and not private.is_symlink(),
                "g2_private_directory_must_be_fresh_runner_child")
        private.mkdir(mode=0o700)
        session = cls(data, private, wav, provenance)
        session.melt_alias_observation = alias_observation
        new_bytes(private / "original-source.wav", wav)
        new_json(private / "input-custody.json", {
            "schema": "motionwright.native.mlt-g2-input-custody/1",
            "current_motionwright_sha": source_sha, "semwright_sha": semwright_sha,
            "run_id": data["run_id"], "run_attempt": data["run_attempt"],
            "original_source": provenance, "source_compiler": closure,
            "official_runtime_receipt_sha256": data["official_runtime_receipt_sha256"],
            "installation_receipt_sha256": data["installation_receipt_sha256"],
            "known_original_module_sha256": data["known_original_module_sha256"],
            "known_melt_sha256": data["known_melt_sha256"],
            "installed_melt_alias": alias_observation,
            "native_effect_started": False,
        })
        return session

    def seed_environment(self, env: dict) -> dict:
        return {
            **env,
            "MOTIONWRIGHT_MLT_ORIGINAL_WAV": str(self.evidence_dir / "original-source.wav"),
            "MOTIONWRIGHT_MLT_ORIGINAL_WAV_SHA256": REFERENCE_WAV_SHA256,
        }

    def secondary(self, stage: str) -> None:
        self.secondary_failures.append(stage)
        warn_secondary()

    def retain_intermediate_output(self) -> None:
        if self.observer is None:
            return
        try:
            self.observer.retain_intermediate(self.evidence_dir)
        except Exception as error:
            self.observer_error_details["original_consumer_output_custody"] = bounded_error(error)
            self.secondary("original_consumer_output_custody")

    def retain_observer_diagnostic(self) -> None:
        if self.observer is None or self.observer_diagnostic_attempted:
            return
        self.observer_diagnostic_attempted = True
        try:
            value = self.observer.diagnostic()
            packed = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
            require(len(packed) <= 3 * 1024 * 1024, "g2_observer_diagnostic_budget")
            new_bytes(self.evidence_dir / "observer-diagnostic.json", packed)
            self.observer_diagnostic_sha256 = sha(packed)
            if not value["bounded_journal_complete"]:
                self.secondary("observer_diagnostic_incomplete")
        except Exception as error:
            self.observer_error_details["diagnostic_custody"] = bounded_error(error)
            self.secondary("observer_diagnostic_custody")

    def run_original(self, run, daemon_pid: int, output_root: Path) -> dict:
        require(not self.assembled_once, "g2_original_assemble_may_only_be_invoked_once")
        self.assembled_once = True
        try:
            self.observer = LiveObserver(daemon_pid, output_root, self.inputs["known_melt_sha256"])
            self.observer.start()
        except BaseException as error:
            self.observer_error_details["observer_start"] = bounded_error(error)
            self.retain_observer_diagnostic()
            raise
        try:
            result = run()
        except BaseException as error:
            self.native_error, self.native_traceback = error, error.__traceback__
            try:
                self.observer.stop()
            except Exception as stop_error:
                self.observer_error_details["observer_stop_after_native_error"] = bounded_error(stop_error)
                self.secondary("observer_stop_after_native_error")
            self.retain_intermediate_output()
            self.retain_observer_diagnostic()
            try:
                new_json(self.evidence_dir / "native-terminal.json", {
                    "terminal_scope": TERMINAL_SCOPE,
                    "native_terminal": "ERROR_OR_UNKNOWN",
                    "exception_type": type(error).__name__,
                    "canonical_failure_diagnostic": (
                        error.diagnostic if isinstance(error, NativeCommandFailure) else None
                    ),
                    "native_operation_retried": False,
                })
            except Exception:
                self.secondary("native_error_custody")
            try:
                if len(self.observer.outputs) == 1:
                    output = Path(next(iter(self.observer.outputs.values())))
                    if output.is_file():
                        packed = admission.checked_bytes(output, 256_000_000)
                        new_bytes(self.evidence_dir / "unconfirmed-native-output.mp4", packed)
                        new_json(self.evidence_dir / "unconfirmed-output.json", {
                            "sha256": sha(packed), "bytes": len(packed),
                            "native_terminal": "ERROR_OR_UNKNOWN", "artifact_accepted": False,
                        })
            except Exception:
                self.secondary("unconfirmed_native_output_custody")
            raise
        # Keep the exact parsed assemble response before every later observation,
        # file read, decode, or receipt write. The command may have failed after
        # an internal RPC succeeded; an absent response never proves its terminal.
        self.native_result = result
        try:
            new_json(self.evidence_dir / "native-terminal.json", {
                "terminal_scope": TERMINAL_SCOPE,
                "native_terminal": ("SUCCESS_REPORTED" if result.get("native_mlt_multisegment_e2e") == "PASS"
                                    else "RESULT_REPORTED_UNVERIFIED"),
                "native_result": result, "native_operation_retried": False,
            })
        except Exception:
            self.secondary("native_success_custody")
        try:
            self.observer.stop()
        except Exception as error:
            self.observer_error_details["observer_stop_after_native_success"] = bounded_error(error)
            self.secondary("observer_stop_after_native_success")
        self.retain_intermediate_output()
        self.retain_observer_diagnostic()
        try:
            relative = result.get("master_artifact_path")
            require(type(relative) is str and relative.endswith(".mp4")
                    and not Path(relative).is_absolute() and ".." not in Path(relative).parts,
                    "g2_reported_master_path_invalid")
            candidate = output_root / relative
            require(output_root.resolve(strict=True) in candidate.resolve(strict=True).parents,
                    "g2_reported_master_outside_owner_root")
            packed = admission.checked_bytes(candidate, 256_000_000)
            require(sha(packed) == result.get("master_artifact_sha256")
                    and len(packed) == result.get("master_artifact_bytes"),
                    "g2_reported_master_bytes_changed")
            new_bytes(self.evidence_dir / "original-native-master.mp4", packed)
            self.master_sha = sha(packed)
        except Exception:
            self.secondary("reported_native_master_custody")
        return result

    def retain_master(self, master: Path, expected_sha: str, expected_size: int) -> None:
        require(self.native_result is not None, "g2_master_without_native_terminal")
        packed = admission.checked_bytes(master, 256_000_000)
        require(sha(packed) == expected_sha and len(packed) == expected_size,
                "g2_native_master_bytes_changed")
        require(self.master_sha == expected_sha
                and admission.checked_bytes(self.evidence_dir / "original-native-master.mp4", 256_000_000) == packed,
                "g2_retained_master_differs_from_native_result")
        try:
            require(self.observer is not None, "g2_original_observer_missing")
            proof = self.observer.require_one(master)
            require(type(proof.get("published_output_sha256")) is str
                    and proof["published_output_sha256"] == expected_sha
                    and type(proof.get("published_output_bytes")) is int
                    and proof["published_output_bytes"] == expected_size,
                    "g2_original_consumer_differs_from_native_result_ack")
            self.live_proof = proof
            new_json(self.evidence_dir / "original-live-module.json", self.live_proof)
        except Exception as error:
            # Keep decoding this one original artifact even if a live witness
            # was missed. Completion still refuses an incomplete evidence chain.
            self.observer_error_details["original_live_module_witness"] = bounded_error(error)
            self.secondary("original_live_module_witness")
            try:
                new_json(self.evidence_dir / "original-live-module-error.json", {
                    "stage": "original_live_module_witness", "error": bounded_error(error),
                    "master_path": str(master), "master_sha256": expected_sha,
                    "master_bytes": expected_size,
                    "observer_diagnostic_sha256": self.observer_diagnostic_sha256,
                    "native_terminal_retained": True, "native_operation_retried": False,
                })
            except Exception as custody_error:
                self.observer_error_details["live_module_error_custody"] = bounded_error(custody_error)
                self.secondary("live_module_error_custody")

    def retain_packet_document(self, raw: bytes) -> None:
        require(0 < len(raw) <= 1_048_576, "g2_packet_document_budget")
        new_bytes(self.evidence_dir / "original-master-packets.json", raw)
        observed = strict_object(raw)
        require(type(observed.get("packets")) is list
                and 0 < len(observed["packets"]) <= 128
                and type(observed.get("streams")) is list
                and len(observed["streams"]) == 1,
                "g2_packet_document_shape")
        self.packet_observation = {
            "ffprobe_document_sha256": sha(raw),
            "packet_count": len(observed["packets"]),
            "packet_PTS_and_AAC_priming_verified": False,
        }

    def inspect_pcm(self, pcm: bytes) -> dict:
        # Import after the canonical producer's functions exist: the historical
        # fixed-fixture envelope deliberately reuses its midstream oracle.
        from native_mlt_ci_pcm_envelope import inspect_fixed_ci_stereo_envelope
        from native_pcm_source_fidelity import inspect_source_pcm16_stereo_content
        require(self.native_result is not None and self.master_sha is not None,
                "g2_pcm_without_retained_native_master")
        new_bytes(self.evidence_dir / "original-master-decoded.s16le", pcm)
        results = {}
        failures = []
        for name, inspect in (
            ("full_source_content", lambda: inspect_source_pcm16_stereo_content(
                self.original_wav, pcm, original_wav_sha256=REFERENCE_WAV_SHA256,
                decoded_pcm_sha256=sha(pcm))),
            ("fixed_fixture_envelope", lambda: inspect_fixed_ci_stereo_envelope(self.original_wav, pcm)),
        ):
            try:
                results[name] = {"state": "PASS", "witness": inspect()}
            except (AssertionError, ValueError) as error:
                results[name] = {"state": "FAIL", "reason": str(error)}
                failures.append(name)
        self.pcm_proof = {
            "schema": "motionwright.native.mlt-g2-source-content/1",
            "master_sha256": self.master_sha, "decoded_pcm_sha256": sha(pcm),
            "original_wav_sha256": REFERENCE_WAV_SHA256,
            "oracles": results, "source_start_offset_frames_assumed": 0,
            "packet_PTS_and_AAC_priming_verified": False,
            "physical_AV_master_acceptance": False, "human_acceptance": False,
        }
        new_json(self.evidence_dir / "source-content.json", self.pcm_proof)
        require(not failures, "g2_full_source_content_failed")
        return self.pcm_proof

    def complete(self) -> dict:
        require(self.native_result is not None
                and self.native_result.get("native_mlt_multisegment_e2e") == "PASS"
                and self.live_proof is not None and self.packet_observation is not None
                and self.pcm_proof is not None and not self.secondary_failures,
                "g2_evidence_chain_incomplete")
        require(all(row["state"] == "PASS" for row in self.pcm_proof["oracles"].values()),
                "g2_source_content_not_verified")
        require(sha(admission.checked_bytes(admission.INSTALLED, admission.MAX_ELF_BYTES))
                == REPAIRED_MODULE_SHA256, "g2_module_changed_after_native_effect")
        require(self.melt_alias_observation is not None
                and verified_installed_melt_alias(self.inputs["known_melt_sha256"])
                == self.melt_alias_observation,
                "g2_installed_melt_alias_changed_after_native_effect")
        self.completed = True
        return {
            "g2_source_content_and_live_module": "VERIFIED",
            "g2_original_wav_sha256": REFERENCE_WAV_SHA256,
            "g2_repaired_module_sha256": REPAIRED_MODULE_SHA256,
            "g2_whole_decoded_pcm_sha256": self.pcm_proof["decoded_pcm_sha256"],
            "g2_packet_observation_sha256": self.packet_observation["ffprobe_document_sha256"],
            "g2_packet_PTS_and_AAC_priming_verified": False,
            "g2_physical_AV_master_acceptance": False,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, error, traceback):
        self.retain_observer_diagnostic()
        cleanup_error = None
        if self.observer is not None:
            try:
                self.observer.close()
            except Exception as close_error:
                cleanup_error = close_error
                self.observer_error_details["observer_descriptor_close"] = bounded_error(close_error)
                self.secondary("observer_descriptor_close")
        try:
            new_json(self.evidence_dir / "final-custody.json", {
                "schema": "motionwright.native.mlt-g2-custody/1",
                "terminal_scope": TERMINAL_SCOPE,
                "native_terminal": (
                    ("SUCCESS_REPORTED" if self.native_result.get("native_mlt_multisegment_e2e") == "PASS"
                     else "RESULT_REPORTED_UNVERIFIED") if self.native_result is not None
                    else "ERROR_OR_UNKNOWN" if self.native_error is not None else "NOT_STARTED"
                ),
                "native_result": self.native_result,
                "canonical_failure_diagnostic": (
                    self.native_error.diagnostic
                    if isinstance(self.native_error, NativeCommandFailure) else None
                ),
                "secondary_failures": self.secondary_failures,
                "observer_error_details": self.observer_error_details,
                "observer_diagnostic_sha256": self.observer_diagnostic_sha256,
                "post_native_error_type": None if error is None else type(error).__name__,
                "source_content_and_live_module_verified": (
                    self.completed and error is None and not self.secondary_failures
                ),
                "original_source": self.provenance,
                "native_operation_retried": False,
                "packet_PTS_and_AAC_priming_verified": False,
                "physical_AV_master_acceptance": False,
                "human_acceptance": False,
            })
        except Exception:
            self.secondary("final_custody")
            if error is None:
                failure = G2Unverified("g2_final_custody_failed")
                failure.native_result = self.native_result
                raise failure
        if self.native_error is not None and error is not self.native_error:
            raise self.native_error.with_traceback(self.native_traceback)
        if cleanup_error is not None and error is None:
            failure = G2Unverified("g2_original_consumer_descriptor_cleanup_failed")
            failure.native_result = self.native_result
            raise failure from cleanup_error
        if error is not None and self.native_result is not None:
            try:
                error.motionwright_native_result = self.native_result
            except Exception:
                pass
        return False
