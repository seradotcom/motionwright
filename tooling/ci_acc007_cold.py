#!/usr/bin/env python3
"""Conditional private ACC007 cold probe using existing process/custody engines.

No dispatch, downloads, retry, SDK authority, installation acceptance or release
admission. The authorized freeze digest must arrive through A1's independent
channel. --launch and --inside are exec-only adapters, not process schedulers.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import time

SCHEMA = "motionwright.acc007-cold-execution/2"
SCOPE = "alignment_cold4gib"
HASH = re.compile(r"[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
BWRAP_VERSION = b"bubblewrap 0.12.0\n"
BWRAP_BINARY = (80248, "573236e5328ac2ebb08f59ae3a9805b4f8d12bdef14be8af4450d5463294985f")
BWRAP_SOURCE = "https://sources.debian.org/src/bubblewrap/0.12.0-1~deb13u1/bubblewrap.c/"
PID_REFERENCE = "https://man7.org/linux/man-pages/man7/pid_namespaces.7.html"
LIMITS = {"as_bytes": 4294967296, "cpu_seconds": 180, "wall_seconds": 180,
          "cleanup_ceiling_seconds": 15, "file_bytes": 262144}
THREADS = {k: "2" for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
SOURCE_FILES = (
    ".github/workflows/performance-acceptance.yml", "SOURCE_LOCK.json",
    "tooling/ci_acc007_cold.py", "tooling/tests/test_ci_acc007_cold.py",
    "tooling/ci_storage_apply_recovery.py", "tooling/ci_private_evidence.py",
    "tooling/mlt_official_runtime_intake.py",
    "tooling/ci_test_runner.py", "tooling/source_policy.py", "tooling/acceptance_policy.py",
    "tooling/creative_status_policy.py", "tooling/public_surface_policy.py",
    "tooling/requirements/mlt-g2-custody.txt",
)
PRIMITIVE_PINS = {
    "tooling/ci_storage_apply_recovery.py": "9aea9a41d365a36fd1abae86ec5a0974236d62d9781a372eace0578ef2da1520",
    "tooling/ci_private_evidence.py": "2671eea354f672353a6b4ea1ac9c43de4b682e96f4dcc9146104a5f8d20a8eee",
    "tooling/mlt_official_runtime_intake.py": "a5c8843db38e48060f34e73afdcaf59341cac0ee88ddd6e13cbf85dadbfb770f",
}
FIXTURE_PINS = {'EXTERNAL-BLOBS.json': (793, 'de41e2a42258e52dee8c29eefe3e9f67eb8a952db44f85d65694f1dd9b003007'), 'G0.json': (810, '0f111dae088145f85215344854f2f59a57d6e56e7b05e1961c0462bca546e2d0'), 'G0.stderr.original': (0, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'), 'G0.stdout.original': (775, '0d868ff4c837d6c1efa665b3f607cf185c6a4ddb1c0cceef3d0387e1220a0b67'), 'g0_check.py': (2025, '8e9593bba4ec5bec3b62f52da38f3700661a2315b62655cdb4bb38488e80a82f'), 'harness/cold_alignment_fixture.py': (18578, '5c908ae2128b6a22ce50a254a0e436ae5f1893cc9589a7e360627210503f66e0'), 'input/ATTRIBUTION.txt': (836, '8f7a28d1a7030512be2fca56bcb6f32f2b462bafa613799eb16730f20211538f'), 'input/CC-BY-4.0.txt': (18657, '9ba9550ad48438d0836ddab3da480b3b69ffa0aac7b7878b5a0039e7ab429411'), 'input/INPUT.json': (3475, '54f758cd18ced7b135eeddfb546b9070c0b92702acbf115deab2536f83ebc2a9'), 'input/MODEL.json': (2083, '243e68f71bee70118b015b53fe036863bdccb265ae111025fdbe6b8138afd9ae'), 'input/reference.txt': (46, '348b9f31e810a5ab7f2442f1eece7a084174ddd4b0fc4fdf37714965730d6e3c'), 'input/TORCHAUDIO-LICENSE.txt': (1338, '93a58861a858cc108e6b6b833e08e76e8b2a66339e4a8007c8a5a8c1ff9c40d6'), 'input/VOICES-LICENSE-SOURCE.html': (51778, '88ce81db797a668136452c7cb4a75ba885d0fbd3da522eec579e57a34f220e6a'), 'input/voices-original.wav': (108844, 'c65fcd726d6b08c82c1e5dc7558f863cd8d483e3ed2f4a7bcf271dc1865ada14'), 'ISSUED.json': (149, '6d4e7fc01d28a692a536fb7fa1bf9034234337231310b3169e940d6e73877e5c'), 'profile/alignment_contract.py': (17123, 'cf63e26be28d143e1ee541e93ce5d55c3f438ac9edb791ae6153f4764041ded6'), 'profile/alignment_runtime_bridge.py': (24462, 'ffca843d76030f264bd017285154f2cbb7fca8818e7eeed8d04d7e7715d8e15b'), 'profile/audio_alignment_worker.py': (11147, '60cfbdfe02796b0ee6333808a0c847c1642a0ff80a400e3ee88933570bf5ab42'), 'PROTOCOL.md': (8709, '185264c1cd0692c3eb60dfa4862143a1d5e1604d433b5dec6281871851170a17'), 'runtime/DEPENDENCY-CLOSURE.json': (1779, '2153e3c5ee9511c5988b780ba26e0f9594ca49fdcf2c2bf80ef06a88641c6576'), 'runtime/RESULT.json': (470, 'a5dd5bd38ddb76a93682bd652b8d5c8a3d1b61f68dba5b4cfa5acb3e83a05b91'), 'runtime/RUNTIME-LINUX-CPU-R2.json': (32989, 'f6ff25fdee612dfd343bd578786a6b0f001dbf8fc65c2a7e0a3748bc0fb7b0ce'), 'runtime/runtime-linux-cpu-r2.lock.txt': (1152, '930f160ab8e3ff6098081c8637a64113cb3e37dd2caa0da038fe3b374dfdedae')}
MODEL = (377664473, "488fd4f16de84438ffc945334278c1b9fb9b7159a806c1080b16111a958c945d")
ARCHIVE = (164045, "225916f26c139c6fbce72af09a50e6cd67053b9322c051c8b0d96fe2058bc7eb")


class Rejected(ValueError):
    pass


class Uncertain(RuntimeError):
    def __init__(self, code, command=None):
        super().__init__(code)
        self.command = command


def require(ok, code):
    if not ok:
        raise Rejected(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode()


def strict(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 2 * 1024 * 1024, "json_size")
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "json_duplicate")
            out[key] = value
        return out
    result = json.loads(raw, object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(Rejected("json_constant")))
    require(type(result) is dict, "json_object")
    return result


def canonical_path(value, *, exists=True):
    p = Path(value)
    require(p.is_absolute() and ".." not in p.parts and "\n" not in str(p), "path_absolute")
    parent = p if p.is_dir() else p.parent
    for component in (parent, *parent.parents):
        require(component.is_dir() and not component.is_symlink(), "path_parent_link")
    require(not p.is_symlink() and (not exists or p.exists()), "path_absent_or_link")
    require(p.resolve(strict=exists) == p, "path_not_canonical")
    return p


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def physical(path, *, limit=256 * 1024 * 1024, keep=False):
    """Stable streaming hash; large model stays outside the bounded custody tar."""
    path = canonical_path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and 0 <= before.st_size <= limit, "regular_single_link_bounded")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    hasher, length, chunks = hashlib.sha256(), 0, []
    try:
        require(identity(os.fstat(fd)) == identity(before), "physical_open_changed")
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            length += len(block)
            require(length <= limit, "physical_grew")
            hasher.update(block)
            if keep:
                chunks.append(block)
        require(identity(os.fstat(fd)) == identity(before), "physical_read_changed")
    finally:
        os.close(fd)
    require(identity(path.lstat()) == identity(before) and length == before.st_size, "physical_path_changed")
    result = {"path": str(path), "bytes": length, "sha256": hasher.hexdigest(), "identity": identity(before)}
    return (result, b"".join(chunks)) if keep else result


def pinned(path, expected):
    require(type(expected) is dict and set(expected) == {"bytes", "sha256"}
            and type(expected["bytes"]) is int and expected["bytes"] >= 0
            and type(expected["sha256"]) is str and HASH.fullmatch(expected["sha256"]), "pin_fields")
    result = physical(path, limit=expected["bytes"])
    require((result["bytes"], result["sha256"]) == (expected["bytes"], expected["sha256"]), "pin_differs")
    return result


def pin_tuple(value):
    return {"bytes": value[0], "sha256": value[1]}


def new_bytes(path, raw):
    path = canonical_path(path, exists=False)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as out:
            out.write(raw)
            out.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    require(physical(path, limit=len(raw), keep=True)[1] == raw, "new_write_readback")


def no_ml():
    require(not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules
                    for prefix in ("torch", "torchaudio", "numpy")), "ml_import_in_supervisor")


def source_lock_pin(source):
    lock = strict(physical(Path(source) / "SOURCE_LOCK.json", keep=True)[1])
    value = lock["dependencies"]["semwright"]["revision"]
    require(type(value) is str and SHA.fullmatch(value), "source_lock_semwright_pin")
    return value


def fixture_originals(archive, root):
    """The small frozen ZIP is private evidence; no extraction or reinterpretation."""
    import io
    import zipfile
    observed, raw = physical(archive, limit=ARCHIVE[0], keep=True)
    require((observed["bytes"], observed["sha256"]) == ARCHIVE, "fixture_archive_changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        names = bundle.namelist()
        require(len(names) == len(set(names)) == 24 and set(names) == set(FIXTURE_PINS) | {"MANIFEST.json"}, "fixture_zip_roster")
        require(sum(x.file_size for x in bundle.infolist()) <= 2 * 1024 * 1024, "fixture_zip_size")
        require(bundle.testzip() is None, "fixture_zip_crc")
        result = {}
        for name in sorted(names):
            original = bundle.read(name)
            _, physical_raw = physical(Path(root) / name, limit=len(original), keep=True)
            require(physical_raw == original, "fixture_extraction_differs")
            result[name] = original
    return raw, result


def context(environ):
    out = dict(zip(("source_sha", "workflow_ref", "run_id", "run_attempt", "job"),
                   (environ.get(k, "") for k in ("GITHUB_SHA", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
                    "GITHUB_RUN_ATTEMPT", "GITHUB_JOB"))))
    require(SHA.fullmatch(out["source_sha"]) is not None, "source_sha")
    require(out["workflow_ref"].startswith("seradotcom/motionwright/.github/workflows/performance-acceptance.yml@")
            and out["job"] == "waveform-lazy", "workflow_binding")
    require(re.fullmatch(r"[1-9][0-9]*", out["run_id"]) and out["run_attempt"] == "1", "no_replayed_run")
    return out


def validate_freeze(value, current):
    require(type(value) is dict and set(value) == {
        "schema", "scope", "source_sha", "workflow_ref", "authorization_record", "private_input_channel",
        "recipient", "repo", "fixture_root", "fixture_archive", "checkpoint", "wheelhouse",
        "execution_root", "source_files", "tools",
    }, "freeze_fields")
    require(value["schema"] == "motionwright.acc007-cold-freeze/2" and value["scope"] == SCOPE, "freeze_scope")
    require(value["source_sha"] == current["source_sha"] and value["workflow_ref"] == current["workflow_ref"], "freeze_source_run_binding")
    for key in ("authorization_record", "private_input_channel"):
        record = value[key]
        require(type(record) is dict and set(record) == {"reference", "sha256"}
                and type(record["reference"]) is str and record["reference"]
                and type(record["sha256"]) is str and HASH.fullmatch(record["sha256"]), "external_record_required")
    require(type(value["recipient"]) is dict and set(value["recipient"]) == {"public_key", "custodian_record"}
            and type(value["recipient"]["custodian_record"]) is str and value["recipient"]["custodian_record"], "real_custodian_record_required")
    require(type(value["source_files"]) is dict and set(value["source_files"]) == set(SOURCE_FILES), "source_roster")
    require(type(value["tools"]) is dict and set(value["tools"]) == {"python", "bwrap", "time", "git"}, "tool_roster")
    for tool in value["tools"].values():
        require(type(tool) is dict and set(tool) == {"path", "bytes", "sha256"}, "tool_pin_fields")
    require(type(value["tools"]["bwrap"]["bytes"]) is int
            and (value["tools"]["bwrap"]["bytes"], value["tools"]["bwrap"]["sha256"]) == BWRAP_BINARY,
            "reviewed_bwrap_binary_required")
    return value


def process_success(command):
    if str(command.get("observation", "")).startswith("UNKNOWN"):
        raise Uncertain("unknown_process_no_retry", command)
    require(command.get("observation") == "DIRECT_PROCESS_EXIT_OBSERVED"
            and type(command.get("exit_code")) is int and command["exit_code"] == 0
            and command.get("owned_group_after_wait") == "ABSENT"
            and command.get("log_custody") == "VERIFIED_SNAPSHOT", "command_not_known_success")


def status_documents(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 16384, "bwrap_status_size")
    rows = [strict(line) for line in raw.splitlines() if line]
    starts = [x for x in rows if "child-pid" in x]
    ends = [x for x in rows if "exit-code" in x]
    require(len(starts) == len(ends) == 1 and rows.index(starts[0]) < rows.index(ends[0]), "bwrap_status_sequence")
    start, end = starts[0], ends[0]
    require(type(start["child-pid"]) is int and start["child-pid"] > 1
            and type(end["exit-code"]) is int and end["exit-code"] == 0, "bwrap_terminal")
    for name in ("pid", "net", "mnt"):
        require(type(start.get(name + "-namespace")) is int and start[name + "-namespace"] > 0, "bwrap_namespace_missing")
    return start, end


def terminal_proof(command, status_raw, boundary, plan, *, child_absent):
    """Additional namespace proof, without rewriting generic NOT_OBSERVED."""
    process_success(command)
    start, _ = status_documents(status_raw)
    require(child_absent is True, "namespace_init_still_present")
    require(boundary.get("schema") == "motionwright.acc007-cold-boundary/2"
            and boundary.get("plan_sha256") == digest(encoded(plan)) and boundary.get("pid") == 1
            and type(boundary.get("pid")) is int and boundary.get("status_fd_closed") is True, "namespace_witness_binding")
    for name in ("pid", "net", "mnt"):
        require(type(boundary.get("namespaces", {}).get(name)) is int
                and boundary["namespaces"][name] == start[name + "-namespace"]
                and boundary["namespaces"][name] != plan["host_namespaces"][name], "namespace_identity")
    require(boundary.get("limits") == {"as": [LIMITS["as_bytes"]] * 2, "cpu": [180, 180],
                                       "fsize": [262144, 262144], "core": [0, 0]}, "observed_limits")
    require(all(type(v) is int for values in boundary["limits"].values() for v in values), "limit_boolean")
    require(boundary.get("threads") == THREADS and boundary.get("network_denied") is True
            and boundary.get("read_only_verified") is True and boundary.get("unexpected_fds") == []
            and type(boundary.get("capabilities")) is int and boundary["capabilities"] == 0
            and type(boundary.get("no_new_privs")) is int and boundary["no_new_privs"] == 1, "boundary_incomplete")
    require(boundary.get("request_sha256") == plan["request_sha256"]
            and boundary.get("request_identity") == plan["request_identity"], "stdin_preseal_differs")
    return {"status": "PID_NAMESPACE_INIT_TERMINATION_OBSERVED", "initial_host_pid": start["child-pid"],
            "namespace_inode": start["pid-namespace"], "original_process_escaped_descendants": command.get("escaped_descendants"),
            "additional_basis": "observed_pid1_and_bwrap_waitpid_with_kernel_pid_namespace_contract",
            "kernel_contract_reference": PID_REFERENCE, "bwrap_source": BWRAP_SOURCE}


def resource_values(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 16384, "time_original_size")
    fields = {}
    keys = ("User time (seconds)", "System time (seconds)", "Maximum resident set size (kbytes)",
            "Exit status", "Elapsed (wall clock) time (h:mm:ss or m:ss)")
    for line in raw.decode("ascii").splitlines():
        for key in keys:
            if line.strip().startswith(key + ":"):
                require(key not in fields, "duplicate_resource_field")
                fields[key] = line.strip()[len(key) + 1:].strip()
    require(len(fields) == 5 and fields["Exit status"] == "0", "resource_roster_or_exit")
    cpu = float(fields[keys[0]]) + float(fields[keys[1]])
    parts = fields[keys[4]].split(":")
    require(len(parts) in (2, 3) and all(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", x) for x in parts), "wall_format")
    wall = sum(float(x) * 60 ** i for i, x in enumerate(reversed(parts)))
    rss = int(fields[keys[2]])
    require(0 <= cpu <= 180 and 0 <= wall <= 180 and 0 < rss <= 4194304, "resource_budget_exceeded")
    return {"cpu_seconds": cpu, "wall_seconds": wall, "max_rss_kib": rss,
            "as_scope": "hard_per_process_RLIMIT_AS", "aggregate_memory_limit_claimed": False}


def closed_read_only_mounts(base_prefix, venv, fixture, checkpoint, seals, adapter,
                           *, legacy_dirs=(), loader_cache=None):
    """Expose nominal fixture files, never their ambient intake directory.

    The runtime base is a canonical installation path observed by prepare().
    System mounts cannot contain any private input: otherwise a parent bind
    would make the individual-file allowlist ineffective. The freshly built
    venv and newly owned seals directory retain their existing closed custody.
    """
    base_prefix, venv, fixture, checkpoint, seals, adapter = map(
        Path, (base_prefix, venv, fixture, checkpoint, seals, adapter))
    private = (venv, fixture, checkpoint, seals, adapter)
    require(all(p.is_absolute() and ".." not in p.parts for p in (base_prefix, *private)),
            "mount_path_not_absolute")
    # Ubuntu's system interpreter or a dedicated /opt tool installation is
    # sufficient for this Linux-only lane. Home/state roots are not runtimes.
    require(base_prefix == Path("/usr") or Path("/usr") in base_prefix.parents
            or Path("/opt") in base_prefix.parents, "runtime_base_prefix_scope")
    legacy = tuple(map(Path, legacy_dirs))
    require(set(legacy) <= {Path(p) for p in ("/bin", "/sbin", "/lib", "/lib64")},
            "unreviewed_system_mount")
    system = sorted({Path("/usr"), base_prefix, *legacy}, key=lambda p: (len(p.parts), str(p)))
    for outer in system:
        require(all(outer != p and outer not in p.parents and p not in outer.parents for p in private),
                "system_mount_overlaps_private_input")
    for outer in (venv, seals):
        require(all(outer != p and outer not in p.parents and p not in outer.parents
                    for p in (fixture, checkpoint, adapter)), "owned_mount_overlaps_frozen_input")
    # Only collapse redundant *system* roots. Never collapse nominal fixture
    # file mounts into a shared ancestor, even if its contents were hash-read.
    selected = []
    for p in system:
        if not any(q == p or q in p.parents for q in selected):
            selected.append(p)
    selected.extend((venv, seals, checkpoint, adapter))
    selected.extend(fixture / name for name in sorted({*FIXTURE_PINS, "MANIFEST.json"}))
    if loader_cache is not None:
        require(Path(loader_cache) == Path("/etc/ld.so.cache"), "unreviewed_system_configuration")
        selected.append(Path(loader_cache))
    require(len(selected) == len(set(selected)), "duplicate_mount_source")
    return [str(p) for p in selected]


def sandbox_argv(plan, status_fd, boundary_fd):
    require(type(status_fd) is int and type(boundary_fd) is int and status_fd > 2
            and boundary_fd > 2 and status_fd != boundary_fd, "launcher_fds")
    args = [plan["bwrap"], "--unshare-user", "--disable-userns", "--unshare-pid", "--as-pid-1",
            "--unshare-net", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--clearenv", "--json-status-fd", str(status_fd),
            "--dir", "/etc"]
    for source in plan["read_only_mounts"]:
        args += ["--ro-bind", source, source]
    for name, target in plan["system_symlinks"].items():
        args += ["--symlink", target, name]
    args += ["--proc", "/proc", "--remount-ro", "/proc", "--dev", "/dev", "--remount-ro", "/dev",
             "--dir", "/run", "--dir", "/tmp", "--bind", plan["rw_source"], plan["attempt"],
             "--remount-ro", "/", "--chdir", plan["attempt"]]
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1", "TMPDIR": plan["attempt"] + "/tmp", **THREADS}
    for key, value in sorted(env.items()):
        args += ["--setenv", key, value]
    return args + ["--", plan["python"], "-I", "-S", "-B", plan["adapter"], "--inside", plan["plan_path"],
                   "--plan-sha256", digest(encoded(plan)), "--boundary-fd", str(boundary_fd), "--status-fd", str(status_fd)]


def read_plan(path, expected):
    _, raw = physical(path, limit=2 * 1024 * 1024, keep=True)
    require(type(expected) is str and HASH.fullmatch(expected) and digest(raw) == expected, "plan_preseal_differs")
    plan = strict(raw)
    require(encoded(plan) == raw and plan.get("schema") == "motionwright.acc007-cold-launch/2", "plan_canonical")
    return plan


def launch(path, expected):
    """Exec-only adapter; the existing execute() owns Popen/wait/cleanup."""
    plan = read_plan(path, expected)
    require(os.getpid() != 1, "launch_inside_namespace")
    opened = []
    for key in ("status_path", "boundary_path"):
        target = canonical_path(plan[key], exists=False)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.set_inheritable(fd, True)
        opened.append(fd)
    args = sandbox_argv(plan, *opened)
    os.execve(plan["bwrap"], args, {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C"})


def namespace_ids():
    return {name: os.stat("/proc/self/ns/" + name).st_ino for name in ("pid", "net", "mnt")}


def inside(path, boundary_fd, status_fd, expected):
    import fcntl
    import mmap
    import resource
    import socket
    plan = read_plan(path, expected)
    no_ml()
    require(os.getpid() == 1 and os.getppid() == 0, "not_namespace_init")
    require(sys.version_info[:2] == (3, 12) and sys.byteorder == "little"
            and platform.machine() == "x86_64" and sys.platform == "linux", "runtime_platform")
    require(sys.executable == plan["python"], "venv_interpreter_changed")
    for name, limit in (("AS", LIMITS["as_bytes"]), ("CPU", 180), ("FSIZE", 262144), ("CORE", 0)):
        resource.setrlimit(getattr(resource, "RLIMIT_" + name), (limit, limit))
    limits = {name.lower(): list(resource.getrlimit(getattr(resource, "RLIMIT_" + name)))
              for name in ("AS", "CPU", "FSIZE", "CORE")}
    try:
        forbidden = mmap.mmap(-1, LIMITS["as_bytes"] + 4096)
    except (OSError, MemoryError) as error:
        require(isinstance(error, MemoryError) or error.errno == errno.ENOMEM, "unexpected_as_failure")
    else:
        forbidden.close()
        raise Rejected("address_space_limit_not_enforced")
    namespace = namespace_ids()
    require(all(namespace[k] != plan["host_namespaces"][k] for k in namespace), "host_namespace_retained")
    ro = lambda p: bool(os.statvfs(p).f_flag & os.ST_RDONLY)
    require(ro("/") and all(ro(p) for p in plan["read_only_checks"]), "read_only_not_enforced")
    require(not ro(plan["attempt"]), "attempt_not_writable")
    try:
        canary = os.open(str(Path(plan["request_path"]).parent / (plan["mode"] + ".forbidden-write")),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        require(error.errno == errno.EROFS, "read_only_write_not_os_denied")
    else:
        os.close(canary)
        raise Rejected("read_only_write_succeeded")
    require({name for _, name in socket.if_nameindex()} == {"lo"}, "unexpected_network_interface")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            probe.connect(("203.0.113.1", 9))
        except OSError as error:
            require(error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPERM, errno.EACCES), "network_not_os_denied")
            network_errno = error.errno
        else:
            raise Rejected("network_escaped")
    try:
        os.fstat(status_fd)
    except OSError as error:
        require(error.errno == errno.EBADF, "status_fd_uncertain")
    else:
        raise Rejected("supervisor_status_fd_leaked")
    unexpected = []
    for item in os.listdir("/proc/self/fd"):
        fd = int(item)
        if fd <= 2 or fd == boundary_fd:
            continue
        try:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        except OSError as error:
            if error.errno == errno.EBADF:
                continue
            raise
        unexpected.append(fd)
    require(not unexpected, "unexpected_inherited_fd")
    status = Path("/proc/self/status").read_text()
    capabilities = int(re.search(r"^CapEff:\s*([0-9a-f]+)$", status, re.M)[1], 16)
    no_new_privs = int(re.search(r"^NoNewPrivs:\s*([0-9]+)$", status, re.M)[1])
    require(capabilities == 0 and no_new_privs == 1, "privilege_boundary")
    observed, raw = physical(plan["request_path"], limit=262144, keep=True)
    require(observed["sha256"] == plan["request_sha256"] and observed["identity"] == plan["request_identity"], "request_preseal_changed")
    fd = os.open(plan["request_path"], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    require(identity(os.fstat(fd)) == observed["identity"], "stdin_descriptor_changed")
    require(digest(os.read(fd, 262145)) == plan["request_sha256"], "stdin_content_changed")
    os.lseek(fd, 0, os.SEEK_SET)
    os.dup2(fd, 0)
    os.close(fd)
    threads = {k: os.environ.get(k) for k in THREADS}
    require(threads == THREADS, "thread_limits")
    witness = {"schema": "motionwright.acc007-cold-boundary/2", "plan_sha256": digest(encoded(plan)),
               "pid": os.getpid(), "namespaces": namespace, "limits": limits, "threads": threads,
               "read_only_verified": True, "network_denied": True, "network_errno": network_errno,
               "status_fd_closed": True, "unexpected_fds": unexpected, "capabilities": capabilities,
               "no_new_privs": no_new_privs, "request_sha256": observed["sha256"], "request_identity": observed["identity"]}
    with os.fdopen(boundary_fd, "wb", closefd=True) as stream:
        stream.write(encoded(witness))
        stream.flush()
        os.fsync(stream.fileno())
    # Witness FD is closed before either a synthetic child or ML can run.
    if plan["mode"] == "control":
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            os.setsid()
            os.write(write_fd, encoded({"pid": os.getpid(), "sid": os.getsid(0)}))
            os.close(write_fd)
            while True:
                time.sleep(1)
        os.close(write_fd)
        child_ready = strict(os.read(read_fd, 4096))
        os.close(read_fd)
        require(child_ready == {"pid": child, "sid": child}, "setsid_child_not_observed")
        os.write(1, encoded({"control": "fork_setsid_descendant", "child_namespace_pid": child,
                             "child_session_id": child_ready["sid"],
                             "as_negative": "ENOMEM", "network_negative_errno": network_errno,
                             "pid1": 1, "ml_imports": False}))
        return 0
    require(plan["mode"] == "inference", "launch_mode")
    no_ml()
    os.execve(plan["python"], [plan["python"], "-I", "-B", plan["bridge"], "--emit-runtime-envelope",
              "--source", plan["attempt"] + "/voices-original.wav", "--checkpoint", plan["checkpoint"],
              "--staging-dir", plan["attempt"]], dict(os.environ))


class Gate:
    def __init__(self, freeze, raw_freeze, current, *, process_engine=None, custody_engine=None):
        self.freeze, self.raw_freeze, self.current = freeze, raw_freeze, current
        self.source, self.root = Path(freeze["repo"]), Path(freeze["execution_root"])
        self.payload = self.root / "private-originals"
        self.logs, self.seals = self.payload / "commands", self.payload / "seals"
        self.attempt, self.control_dir = self.payload / "attempt", self.payload / "boundary-control"
        self.venv = self.root / "runtime"
        self.python = str(self.venv / "bin/python3.12")
        self.process, self.custody = process_engine, custody_engine
        self.commands, self.inference_count, self.before, self.sealed_files = [], 0, {}, {}
        self.unknown, self.after_started = False, False
        self.result = {"schema": SCHEMA, "scope": SCOPE, "context": current, "status": "PREPARING",
                       "SDK_authority": False, "backend_authority": False, "human_acceptance": False,
                       "release_authorized": False, "automatic_retry": False,
                       "inference_attempts": 0, "inference_invocations": 0,
                       "existing_cleanup_wait_limits_seconds": [2, 2],
                       "requested_cleanup_ceiling_seconds": 15,
                       "cleanup_duration_separately_observed": False}

    def env(self):
        return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C",
                "TMPDIR": str(self.root / "tmp"), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                "PIP_CONFIG_FILE": "/dev/null", "PIP_CACHE_DIR": str(self.root / "pip-cache"),
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", **THREADS}

    def execute(self, args, label, timeout=45):
        require(not self.unknown, "no_command_after_unknown")
        started = time.monotonic()
        try:
            row = self.process.execute(args, self.source, self.env(), self.logs, label, timeout, self.current)
        except self.process.UnknownObservation as error:
            self.unknown = True
            raise Uncertain("original_receipt_unknown", getattr(error, "command", None)) from error
        finally:
            self.result.setdefault("command_elapsed_observations", []).append({
                "label": label, "wait_timeout_seconds": timeout,
                "supervisor_call_seconds": time.monotonic() - started,
                "scope": "launch_wait_cleanup_and_original_capture_combined"})
        self.commands.append(row)
        if self.process.execution_unknown(row):
            self.unknown = True
            raise Uncertain("original_command_unknown", row)
        process_success(row)
        return row

    def original(self, row, stream):
        return self.process.read_original(self.logs, row, stream)

    def prepare(self):
        f = self.freeze
        no_ml()
        require(sys.platform == "linux" and platform.machine() == "x86_64", "linux_x86_64_required")
        source = canonical_path(f["repo"])
        require(source.is_dir(), "repo_missing")
        for name, pin in f["source_files"].items():
            self.before["source/" + name] = pinned(source / name, pin)
        for name, expected in PRIMITIVE_PINS.items():
            require(self.before["source/" + name]["sha256"] == expected, "shared_primitive_changed_requires_review")
        for name, pin in f["tools"].items():
            self.before["tool/" + name] = pinned(pin["path"], {k: pin[k] for k in ("bytes", "sha256")})
            require(os.access(pin["path"], os.X_OK), "tool_not_executable")
        # The dynamic loader may need this one public system input. No host
        # /etc tree, credentials, preload configuration or pathname sockets
        # are exported. Record its physical identity for the same postcheck.
        loader_cache = Path("/etc/ld.so.cache")
        if loader_cache.exists() or loader_cache.is_symlink():
            self.before["system/ld.so.cache"] = physical(loader_cache, limit=2 * 1024 * 1024)
        fixture = canonical_path(f["fixture_root"])
        require(len(FIXTURE_PINS) == 23, "frozen_fixture_roster_missing")
        for name, pin in FIXTURE_PINS.items():
            self.before["fixture/" + name] = pinned(fixture / name, pin_tuple(pin))
        self.before["fixture-archive"] = pinned(f["fixture_archive"], pin_tuple(ARCHIVE))
        archive_raw, fixture_bytes = fixture_originals(f["fixture_archive"], fixture)
        self.before["checkpoint"] = pinned(f["checkpoint"], pin_tuple(MODEL))
        _, raw = physical(fixture / "runtime/RUNTIME-LINUX-CPU-R2.json", limit=1048576, keep=True)
        runtime = strict(raw)
        require(runtime.get("cpu_only") is True and runtime.get("installed_on_host") is False
                and runtime.get("inference_executed") is False, "runtime_intake_provenance")
        wheels = runtime.get("wheels")
        require(type(wheels) is list and len(wheels) == 12, "runtime_wheel_count")
        wheelhouse = canonical_path(f["wheelhouse"])
        paths = sorted(wheelhouse.glob("*.whl"))
        require(len(paths) == 12 and len(list(wheelhouse.iterdir())) == 12, "wheelhouse_closed_roster")
        actual = {physical(p)["sha256"]: p for p in paths}
        require(set(actual) == {x["sha256"] for x in wheels}, "wheelhouse_pins")
        for wheel in wheels:
            self.before["wheel/" + wheel["name"]] = pinned(actual[wheel["sha256"]],
                {k: wheel[k] for k in ("bytes", "sha256")})
        root = canonical_path(self.root, exists=False)
        require(not root.exists(), "attempt_root_exists_no_retry")
        protected = [source, fixture, wheelhouse, Path(f["checkpoint"]), Path(f["fixture_archive"])]
        require(not any(root == p or root in p.parents or p in root.parents for p in protected), "execution_root_overlap")
        import shutil
        require(shutil.disk_usage(root.parent).free >= 6 * 1024 ** 3, "runner_disk_budget")
        mem = re.search(r"^MemAvailable:\s+([0-9]+) kB$", Path("/proc/meminfo").read_text(), re.M)
        require(mem and int(mem[1]) >= 5 * 1024 ** 2, "runner_memory_budget")
        # Resolve the seal's source context before creating any output. A
        # malformed lock cannot leave a payload that has no valid seal context.
        self.sem_sha = source_lock_pin(source)
        if self.process is None:
            import ci_storage_apply_recovery as process_engine
            self.process = process_engine
        if self.custody is None:
            import ci_private_evidence as custody_engine
            self.custody = custody_engine
        self.custody.public_key(f["recipient"]["public_key"])
        root.mkdir(mode=0o700)
        for directory in (self.payload, self.logs, self.seals, self.attempt, self.control_dir,
                          self.attempt / "tmp", self.control_dir / "tmp", root / "tmp", root / "pip-cache"):
            directory.mkdir(mode=0o700)
        new_bytes(self.seals / "authorized-freeze.original.json", self.raw_freeze)
        new_bytes(self.seals / "source-inputs.before.json", encoded(self.before))
        private_inputs = self.payload / "fixture-originals"
        private_inputs.mkdir(mode=0o700)
        new_bytes(private_inputs / "capsule.original.zip", archive_raw)
        for name, raw in fixture_bytes.items():
            destination = private_inputs / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            new_bytes(destination, raw)
        git = f["tools"]["git"]["path"]
        row = self.execute([git, "--no-replace-objects", "-c", "core.fsmonitor=false", "rev-parse", "HEAD"], "source-head")
        require(self.original(row, "stdout") == (self.current["source_sha"] + "\n").encode(), "head_differs")
        for i, name in enumerate(SOURCE_FILES):
            row = self.execute([git, "--no-replace-objects", "cat-file", "blob", self.current["source_sha"] + ":" + name], "source-blob-%02d" % i)
            raw = self.original(row, "stdout")
            require(digest(raw) == self.before["source/" + name]["sha256"]
                    and len(raw) == self.before["source/" + name]["bytes"], "physical_source_not_git_blob")
        row = self.execute([f["tools"]["bwrap"]["path"], "--version"], "bwrap-version")
        require(self.original(row, "stdout") == BWRAP_VERSION, "bwrap_version_requires_review")
        base = f["tools"]["python"]["path"]
        self.execute([base, "-I", "-B", "-m", "venv", str(self.venv)], "runtime-venv", timeout=90)
        lock = str(fixture / "runtime/runtime-linux-cpu-r2.lock.txt")
        self.execute([self.python, "-I", "-B", "-m", "pip", "--isolated", "install", "--no-index", "--require-hashes",
                      "--only-binary=:all:", "--find-links", str(wheelhouse), "--no-cache-dir", "-r", lock], "runtime-offline-install", timeout=240)
        self.execute([self.python, "-I", "-B", "-m", "pip", "--isolated", "check"], "runtime-pip-check")
        code = ("import importlib.metadata as m,json,sys,platform; "
                "print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
                "'version':list(sys.version_info[:2]),'machine':platform.machine(),'byteorder':sys.byteorder,"
                "'packages':{d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions()},"
                "'ml_imports':[n for n in sys.modules if n.split('.')[0] in ('torch','torchaudio','numpy')]},sort_keys=True))")
        row = self.execute([self.python, "-I", "-B", "-c", code], "runtime-installed-metadata")
        observed = strict(self.original(row, "stdout"))
        expected = {x["name"].lower().replace("_", "-"): x["version"] for x in wheels}
        require(observed["executable"] == self.python and observed["prefix"] == str(self.venv)
                and observed["base_prefix"] != observed["prefix"] and observed["version"] == [3, 12]
                and observed["machine"] == "x86_64" and observed["byteorder"] == "little"
                and observed["ml_imports"] == [] and all(observed["packages"].get(k) == v for k, v in expected.items())
                and set(observed["packages"]) - set(expected) <= {"pip"}, "same_interpreter_installed_closure")
        self.base_prefix = canonical_path(observed["base_prefix"])
        require(self.base_prefix != Path("/") and self.root not in self.base_prefix.parents, "runtime_base_prefix_scope")
        self.runtime_before = self.runtime_snapshot()
        new_bytes(self.seals / "runtime.before.json", encoded(self.runtime_before))
        self.fixture = fixture
        args = self.fixture_args("build") + ["--source", str(fixture / "input/voices-original.wav"),
                "--reference", str(fixture / "input/reference.txt"), "--source-commit", self.current["source_sha"]]
        row = self.execute(args, "construct-request")
        construction = strict(self.original(row, "stdout"))
        for name in ("request.json", "construction.json"):
            _, raw = physical(self.attempt / name, limit=262144, keep=True)
            new_bytes(self.seals / name, raw)
        self.request = physical(self.seals / "request.json")
        self.construction = physical(self.seals / "construction.json")
        require(construction["request_sha256"] == self.request["sha256"], "constructor_request_binding")
        new_bytes(self.seals / "pre-execute.json", encoded({"context": self.current, "request": self.request,
                  "construction": self.construction, "origin": "ORIGINAL_PREDISPATCH_READBACK", "timing": "BEFORE_EXECUTE"}))

    def runtime_snapshot(self):
        result = {}
        paths = sorted(self.venv.rglob("*"))
        require(len(paths) <= 80000, "runtime_inventory_budget")
        for p in paths:
            info = p.lstat()
            name = p.relative_to(self.venv).as_posix()
            if stat.S_ISDIR(info.st_mode):
                continue
            if stat.S_ISLNK(info.st_mode):
                require(name in ("bin/python", "bin/python3", "bin/python3.12", "lib64"), "runtime_unexpected_symlink")
                target = p.resolve(strict=True)
                result[name] = {"symlink": os.readlink(p), "identity": identity(info),
                                "target": physical(target) if target.is_file() else str(target)}
                continue
            result[name] = physical(p, limit=2 * 1024 ** 3)
        require(paths == sorted(self.venv.rglob("*")), "runtime_inventory_changed")
        cfg = (self.venv / "pyvenv.cfg").read_text()
        require(re.search(r"^include-system-site-packages\s*=\s*false\s*$", cfg, re.M), "system_site_packages_enabled")
        return result

    def fixture_args(self, mode):
        return [self.python, "-I", "-S", "-B", str(self.fixture / "harness/cold_alignment_fixture.py"), mode,
                "--profile-dir", str(self.fixture / "profile"), "--attempt-dir", str(self.attempt),
                "--checkpoint", self.freeze["checkpoint"], "--runtime-lock", str(self.fixture / "runtime/runtime-linux-cpu-r2.lock.txt"),
                "--runtime-manifest", str(self.fixture / "runtime/RUNTIME-LINUX-CPU-R2.json")]

    def plan(self, mode):
        adapter = self.source / "tooling/ci_acc007_cold.py"
        aliases, legacy_dirs = {}, []
        for path in ("/bin", "/sbin", "/lib", "/lib64"):
            p = Path(path)
            if p.is_symlink():
                aliases[path] = os.readlink(p)
            elif p.exists():
                legacy_dirs.append(p)
        cache = self.before.get("system/ld.so.cache")
        if cache is not None:
            require(physical(cache["path"], limit=cache["bytes"]) == cache, "system_cache_changed")
        selected = closed_read_only_mounts(self.base_prefix, self.venv, self.fixture,
            self.freeze["checkpoint"], self.seals, adapter, legacy_dirs=legacy_dirs,
            loader_cache=cache["path"] if cache is not None else None)
        plan = {"schema": "motionwright.acc007-cold-launch/2", "mode": mode, "context": self.current,
                "freeze_sha256": digest(self.raw_freeze), "bwrap": self.freeze["tools"]["bwrap"]["path"],
                "python": self.python, "adapter": str(adapter), "attempt": str(self.attempt),
                "rw_source": str(self.control_dir if mode == "control" else self.attempt),
                "bridge": str(self.fixture / "profile/alignment_runtime_bridge.py"), "checkpoint": self.freeze["checkpoint"],
                "request_path": self.request["path"], "request_sha256": self.request["sha256"],
                "request_identity": self.request["identity"], "host_namespaces": namespace_ids(),
                "read_only_mounts": selected, "system_symlinks": aliases,
                "read_only_checks": [str(self.venv), str(self.fixture), self.freeze["checkpoint"], str(self.seals), str(adapter)],
                "plan_path": str(self.seals / (mode + ".plan.json")),
                "status_path": str(self.logs / (mode + ".bwrap.original.jsonl")),
                "boundary_path": str(self.logs / (mode + ".boundary.original.json"))}
        new_bytes(plan["plan_path"], encoded(plan))
        self.sealed_files[plan["plan_path"]] = physical(plan["plan_path"])
        return plan

    def namespaced(self, mode):
        plan = self.plan(mode)
        if mode == "inference":
            require(self.inference_count == 0, "inference_already_attempted")
            new_bytes(self.seals / "inference.started.json", encoded({"context": self.current, "plan_sha256": digest(encoded(plan))}))
            self.inference_count = 1
            self.result["inference_attempts"] = 1
            self.result["inference_invocations"] = None
        argv = [self.freeze["tools"]["time"]["path"], "-v", "-o", str(self.logs / (mode + ".time.original.txt")),
                self.python, "-I", "-S", "-B", str(self.source / "tooling/ci_acc007_cold.py"), "--launch", plan["plan_path"],
                "--plan-sha256", digest(encoded(plan))]
        started = time.monotonic()
        row = self.execute(argv, mode, timeout=180 if mode == "inference" else 20)
        elapsed = time.monotonic() - started
        require(elapsed <= (180 if mode == "inference" else 20), "observed_wall_exceeded")
        status_observed, status_raw = physical(plan["status_path"], limit=16384, keep=True)
        boundary_observed, boundary_raw = physical(plan["boundary_path"], limit=16384, keep=True)
        start, _ = status_documents(status_raw)
        terminal = terminal_proof(row, status_raw, strict(boundary_raw), plan,
                                  child_absent=not Path("/proc", str(start["child-pid"])).exists())
        time_observed, time_raw = physical(self.logs / (mode + ".time.original.txt"), limit=16384, keep=True)
        for observed in (status_observed, boundary_observed, time_observed):
            self.sealed_files[observed["path"]] = observed
        resources = resource_values(time_raw)
        if mode == "control":
            control = strict(self.original(row, "stdout"))
            require(control.get("control") == "fork_setsid_descendant" and type(control.get("child_namespace_pid")) is int
                    and control["child_namespace_pid"] > 1 and control.get("child_session_id") == control["child_namespace_pid"]
                    and control.get("pid1") == 1 and control.get("ml_imports") is False
                    and control.get("as_negative") == "ENOMEM", "physical_control_incomplete")
        self.result[mode] = {"terminal": terminal, "resources": resources, "supervisor_elapsed_seconds": elapsed,
                             "raw_command_label": mode, "status_original": status_observed,
                             "boundary_original": boundary_observed, "time_original": time_observed}
        return row

    def after(self):
        require(not self.after_started, "post_snapshot_not_retried")
        self.after_started = True
        after, errors = {}, {}
        for name, before in self.before.items():
            try:
                after[name] = physical(before["path"], limit=before["bytes"])
            except Exception as error:
                errors[name] = type(error).__name__ + ": " + str(error)
        new_bytes(self.seals / "source-inputs.after.json", encoded({"observed": after, "errors": errors}))
        require(not errors and after == self.before, "source_or_input_changed")
        if hasattr(self, "runtime_before"):
            runtime = self.runtime_snapshot()
            new_bytes(self.seals / "runtime.after.json", encoded(runtime))
            require(runtime == self.runtime_before, "runtime_changed")
        if hasattr(self, "request"):
            require(physical(self.request["path"]) == self.request and physical(self.construction["path"]) == self.construction,
                    "external_seals_changed")
        for path, observed in self.sealed_files.items():
            require(physical(path, limit=observed["bytes"]) == observed, "supervision_original_changed")
        for command in self.commands:
            for stream in ("stdout", "stderr"):
                self.original(command, stream)

    def run(self):
        self.prepare()
        self.namespaced("control")
        row = self.namespaced("inference")
        args = self.fixture_args("validate") + ["--stdout", str(self.logs / row["stdout"]["path"]),
                "--stderr", str(self.logs / row["stderr"]["path"]), "--expected-request-sha256", self.request["sha256"],
                "--expected-construction-sha256", self.construction["sha256"]]
        validation = self.execute(args, "postvalidate")
        report = strict(self.original(validation, "stdout"))
        require(report.get("status") == "CONSISTENCY_VALID" and report.get("backend_authority") is False
                and report.get("request_sha256") == self.request["sha256"], "postvalidation_not_bound")
        self.after()
        self.result.update(status="TECHNICAL_COLD_FIXTURE_VERIFIED", fixture_seconds=3.4,
                           pcm_frames=54400, model_frames=169, expected_words=9,
                           inference_invocations=1,
                           unresolved_word_count=report.get("unresolved_word_count"),
                           cleanup_on_success="NOT_REQUIRED", cleanup_wait_limits_seconds=[2, 2],
                           cleanup_ceiling_seconds=15, cleanup_observed_on_timeout=False)
        return self.result

    def retain(self):
        """Only read-only reconciliation and a single seal remain after UNKNOWN."""
        if not self.payload.exists():
            return None
        if not self.after_started:
            try:
                self.after()
            except Exception as error:
                self.result["readonly_reconciliation_error"] = type(error).__name__ + ": " + str(error)
        self.result["inference_attempts"] = self.inference_count
        new_bytes(self.payload / "result.original.json", encoded(self.result))
        plaintext, complete = self.custody.gather_snapshot({"attempt": self.payload}, preserve_partial=True)
        seal_context = {k: self.current[k] for k in ("source_sha", "run_id", "run_attempt")}
        seal_context["semwright_sha"] = self.sem_sha
        ciphertext = self.custody.encrypt_snapshot(plaintext, self.freeze["recipient"]["public_key"], seal_context)
        self.custody.new_bytes(self.root / "cold-private-evidence.mwseal", ciphertext)
        execution_status = self.result["status"]
        status = execution_status if complete or execution_status.startswith("UNKNOWN") else "FAIL_INCOMPLETE_CUSTODY"
        public = {"schema": SCHEMA, "context": self.current, "scope": SCOPE,
                  "status": status, "execution_status": execution_status,
                  "custody_status": "COMPLETE" if complete else "INCOMPLETE",
                  "selected_originals_complete": complete, "inference_attempts": self.inference_count,
                  "inference_invocations": self.result["inference_invocations"],
                  "ciphertext_bytes": len(ciphertext), "ciphertext_sha256": digest(ciphertext),
                  "recipient_spki_sha256": self.freeze["recipient"]["public_key"]["sha256"],
                  "model_and_wheels_embedded": False, "SDK_authority": False, "human_acceptance": False,
                  "release_authorized": False, "automatic_retry": False}
        new_bytes(self.root / "cold-public-status.json", encoded(public))
        return public


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--freeze", type=Path)
    group.add_argument("--launch", type=Path)
    group.add_argument("--inside", type=Path)
    parser.add_argument("--authorized-freeze-sha256")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--boundary-fd", type=int)
    parser.add_argument("--status-fd", type=int)
    args = parser.parse_args(argv)
    if args.launch:
        launch(args.launch, args.plan_sha256)
        return 2
    if args.inside:
        return inside(args.inside, args.boundary_fd, args.status_fd, args.plan_sha256)
    gate = None
    try:
        require(type(args.authorized_freeze_sha256) is str and HASH.fullmatch(args.authorized_freeze_sha256), "out_of_band_authorization_digest_required")
        observed, raw = physical(args.freeze, limit=2 * 1024 * 1024, keep=True)
        require(observed["sha256"] == args.authorized_freeze_sha256, "authorized_freeze_differs")
        current = context(os.environ)
        freeze = validate_freeze(strict(raw), current)
        gate = Gate(freeze, raw, current)
        gate.run()
    except Uncertain as error:
        if gate:
            gate.result.update(status="UNKNOWN_NO_RETRY", reason=str(error), command=error.command)
    except Exception as error:
        if gate:
            gate.result.update(status="FAIL" if gate.inference_count else "BLOCKED_PRECONDITION",
                               reason=type(error).__name__ + ": " + str(error))
        else:
            print("ACC007 precondition rejected before effects: " + type(error).__name__, file=sys.stderr)
            return 78
    try:
        public = gate.retain()
    except Exception as error:
        print("ACC007 custody incomplete; originals retained; no retry: " + type(error).__name__, file=sys.stderr)
        return 2
    if public:
        print(json.dumps(public, sort_keys=True))
        return 0 if public["status"] == "TECHNICAL_COLD_FIXTURE_VERIFIED" else 2
    print("ACC007 preconditions unavailable; no inference.")
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
