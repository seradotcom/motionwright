#!/usr/bin/env python3
"""Read-only Linux attestation of an actually mapped MLT avformat module.

A source-level C PASS does not show that the authorized Semwright MLT Host
loaded repaired code. This exact native-process witness accepts no loader
environment override or untrusted module path. A runner must capture a live
original melt PID and attest it BEFORE it exits. It does not start/retry native
effects, install a module, grant permission or approve decoded audio content.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

HEX = re.compile(r"[0-9a-f]{64}\Z")
EXECUTE = frozenset({"r-xp", "r-xs"})
FORBIDDEN_ENV = frozenset({
    "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT",
    "MLT_REPOSITORY", "MLT_DATA", "MLT_PROFILES_PATH",
})
MAX_MAPS = 2 * 1024 * 1024
MAX_CMDLINE = 12 * 1024
MAX_ENV = 64 * 1024
MAX_MODULE = 64 * 1024 * 1024
MAX_MELT = 64 * 1024 * 1024


class LoadedModuleUnverified(ValueError):
    """Observed native process does not prove the admitted repaired module."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise LoadedModuleUnverified(code)


def _binary_generation(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _require_binary_generation(fd: int, path: Path,
                               original: os.stat_result) -> None:
    try:
        opened = os.fstat(fd)
        named = path.lstat()
    except OSError as exc:
        raise LoadedModuleUnverified(
            "binary_replaced_during_loaded_module_read"
        ) from exc
    require(_binary_generation(opened) == _binary_generation(original)
            and _binary_generation(named) == _binary_generation(original),
            "binary_replaced_during_loaded_module_read")


@contextmanager
def _verified_binary(path: Path, expected: str, budget: int):
    """Keep the hashed regular-file descriptor and its identity until exit."""
    require(HEX.fullmatch(expected) is not None, "expected_binary_sha256_invalid")
    require(path.is_absolute() and path.is_file()
            and not path.is_symlink(), "verified_binary_not_regular_absolute")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise LoadedModuleUnverified("verified_binary_open_failed") from exc
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode),
                "verified_binary_not_regular_absolute")
        require(0 < before.st_size <= budget, "verified_binary_budget")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            require(size <= budget, "verified_binary_budget")
            digest.update(chunk)
        require(size == before.st_size,
                "binary_replaced_during_loaded_module_read")
        _require_binary_generation(fd, path, before)
        actual = digest.hexdigest()
        require(actual == expected, "loaded_process_or_MLT_module_sha256_changed")
        yield {"sha256": actual, "stat": before}
        _require_binary_generation(fd, path, before)
    finally:
        os.close(fd)


def sha256(path: Path, expected: str, budget: int) -> str:
    with _verified_binary(path, expected, budget) as observed:
        return observed["sha256"]


def parse_maps(text: str, expected_path: str, device: tuple[int, int],
               inode: int) -> dict:
    """Bind all mapped original module segments to one actual inode/dev pair."""
    require(expected_path.startswith(("/usr/lib/", "/lib/"))
            and expected_path.endswith("/libmltavformat.so"),
            "module_must_use_trusted_system_directory")
    entries = []
    other_modules = []
    for line in text.splitlines():
        bits = line.split(maxsplit=5)
        if len(bits) != 6:
            continue
        address, perms, _offset, majmin, raw_inode, raw_path = bits
        if "libmltavformat.so" not in raw_path:
            continue
        if raw_path != expected_path:
            other_modules.append(raw_path)
            continue
        try:
            major, minor = (int(part, 16) for part in majmin.split(":"))
            observed_inode = int(raw_inode)
            start_hex, end_hex = address.split("-")
            start, end = int(start_hex, 16), int(end_hex, 16)
        except (ValueError, TypeError):
            raise LoadedModuleUnverified("mapped_native_MLT_segment_malformed") from None
        require(start < end and (major, minor) == device
                and observed_inode == inode,
                "mapped_native_MLT_inode_or_device_changed")
        entries.append({
            "address_bytes": end - start,
            "perms": perms, "dev_major": major, "dev_minor": minor,
            "inode": observed_inode,
        })
    require(not other_modules, "alternate_or_deleted_MLT_avformat_module_mapped")
    require(entries and any(entry["perms"] in EXECUTE for entry in entries),
            "actual_native_MLT_avformat_executable_mapping_not_observed")
    return {
        "mapped_segments": len(entries),
        "mapped_executable": True,
        "mapped_module_inode": inode,
        "mapped_device_major_minor": list(device),
        "mapped_segment_perms": [entry["perms"] for entry in entries],
    }


def verified_melt_cmdline(payload: bytes, expected_output: str, *,
                          relative_cwd: str | None = None) -> dict:
    require(0 < len(payload) <= MAX_CMDLINE and payload.endswith(b"\0"),
            "melt_original_process_argv_missing_or_unbounded")
    require(6 < len(expected_output) <= 4096
            and expected_output.startswith("/")
            and not expected_output.startswith("//")
            and "\\" not in expected_output
            and ":" not in expected_output
            and ".." not in expected_output.split("/")
            and expected_output.endswith(".mp4"),
            "selected_native_output_path_not_admitted")
    try:
        argv = [part.decode("utf-8") for part in payload[:-1].split(b"\0")]
    except UnicodeDecodeError as e:
        raise LoadedModuleUnverified("native_MLT_argv_not_utf8") from e
    require(len(argv) >= 4 and argv[0]
            and "-consumer" in argv, "native_melt_consumer_argv_missing")
    consumers = [
        argv[i + 1] for i, item in enumerate(argv[:-1])
        if item == "-consumer"
    ]
    selected_argument = expected_output
    if relative_cwd is not None:
        require(re.fullmatch(r"/tmp/semwright-video-[0-9a-f]{32}", relative_cwd) is not None
                and expected_output == relative_cwd + "/partial.mp4",
                "relative_melt_consumer_requires_exact_private_cwd")
        if consumers == ["avformat:partial.mp4"]:
            selected_argument = "partial.mp4"
    require(consumers == ["avformat:" + selected_argument],
            "live_melt_consumer_does_not_target_the_original_output")
    return {
        "bounded_arg_count": len(argv),
        "actual_original_MLT_consumer": "avformat",
        "selected_original_output_path": expected_output,
        **({"actual_consumer_argument": selected_argument,
            "relative_cwd_binding_required": selected_argument == "partial.mp4"}
           if relative_cwd is not None else {}),
    }


def verified_restricted_environment(payload: bytes) -> dict:
    require(0 < len(payload) <= MAX_ENV and payload.endswith(b"\0"),
            "live_melt_environment_unavailable_or_unbounded")
    raw = payload[:-1].split(b"\0")
    keys = [item.split(b"=", 1)[0].decode("ascii")
            for item in raw if b"=" in item]
    require(not FORBIDDEN_ENV.intersection(keys),
            "native_melt_loaded_module_depends_on_an_untrusted_loader_override")
    return {
        "restricted_loader_overrides_absent": True,
        "observed_process_env_variable_count": len(keys),
    }


def parse_process_starttime(raw: bytes) -> int:
    """Linux /proc/<pid>/stat field 22, robust to parentheses in comm.

    A PID alone is not process identity: compare this value before and after
    all maps/argv/env/digest reads to reject a dead/reused native melt PID.
    """
    require(0 < len(raw) <= 8192, "live_native_proc_stat_invalid")
    try:
        text = raw.decode("ascii").strip()
        _, right = text.rsplit(")", 1)
        rest = right.split()
    except (ValueError, UnicodeDecodeError) as exc:
        raise LoadedModuleUnverified("live_native_proc_stat_malformed") from exc
    # Following fields begin at 3 (state), so field 22 is index 19.
    require(len(rest) >= 20, "live_native_proc_stat_incomplete")
    try:
        starttime = int(rest[19])
    except ValueError as exc:
        raise LoadedModuleUnverified("live_native_proc_stat_malformed") from exc
    require(starttime > 0, "live_native_proc_starttime_invalid")
    return starttime




@contextmanager
def _verified_host_sealed_melt(proc: Path, expected: str):
    """Bind the original process executable to the one Host-sealed melt entry.

    Only the kernel's PID-root link is followed. The fixed namespace components
    must remain ordinary directories/files, and the executable descriptor must
    be the inode exposed by that same live process's exe link.
    """
    for directory in (proc / "root" / "plugin", proc / "root" / "plugin" / "tools"):
        require(directory.is_dir() and not directory.is_symlink(),
                "host_sealed_melt_namespace_directory_invalid")
    path = proc / "root" / "plugin" / "tools" / "melt"
    with _verified_binary(path, expected, MAX_MELT) as executable:
        initial = executable["stat"]
        require(stat.S_IMODE(initial.st_mode) == 0o500
                and initial.st_uid == proc.stat().st_uid,
                "host_sealed_melt_owner_or_mode_changed")
        require(_binary_generation((proc / "exe").stat()) == _binary_generation(initial),
                "host_sealed_melt_is_not_the_live_executable")
        yield executable
        require(_binary_generation((proc / "exe").stat()) == _binary_generation(initial),
                "host_sealed_melt_live_executable_changed")


def inspect_live_original(pid: int, melt_path: Path, melt_sha: str,
                          module_path: Path, module_sha: str,
                          expected_output_path: str, *,
                          host_sealed_melt: bool = False) -> dict:
    require(sys.platform == "linux", "live_native_module_attestation_linux_only")
    require(type(pid) is int and 1 < pid < 2**30,
            "native_melt_pid_invalid")
    require(module_path.is_absolute() and not module_path.is_symlink()
            and module_path.as_posix().startswith("/usr/lib/")
            and module_path.name == "libmltavformat.so",
            "module_must_be_installed_on_the_trusted_runner_system_surface")
    proc = Path("/proc") / str(pid)
    require(proc.is_dir(), "original_native_melt_process_not_live")
    process_start = parse_process_starttime((proc / "stat").read_bytes())
    actual_melt = os.readlink(proc / "exe")
    require(type(host_sealed_melt) is bool, "host_sealed_melt_mode_invalid")
    if host_sealed_melt:
        require(melt_path == Path("/usr/bin/melt-7"),
                "host_sealed_melt_requires_admitted_official_executable")
        cwd_link = os.readlink(proc / "cwd")
        cwd = expected_output_path.rpartition("/")[0]
        require(re.fullmatch(r"/tmp/semwright-video-[0-9a-f]{32}", cwd) is not None
                and expected_output_path == cwd + "/partial.mp4",
                "host_sealed_melt_private_output_changed")
        cwd_identity = (proc / "cwd").stat()
        namespace_cwd = proc / "root" / cwd.lstrip("/")
        require(namespace_cwd.is_dir() and not namespace_cwd.is_symlink()
                and (namespace_cwd.stat().st_dev, namespace_cwd.stat().st_ino)
                    == (cwd_identity.st_dev, cwd_identity.st_ino),
                "host_sealed_melt_cwd_namespace_changed")
        executable_context = _verified_host_sealed_melt(proc, melt_sha)
    else:
        require(actual_melt == str(melt_path.resolve(strict=True)),
                "original_native_melt_process_executable_mismatch")
        executable_context = _verified_binary(melt_path.resolve(strict=True), melt_sha, MAX_MELT)
    with executable_context as exe, \
            _verified_binary(module_path, module_sha, MAX_MODULE) as module:
        # The mapping must refer to the descriptor whose bytes were hashed,
        # not a later generation obtained by looking up the pathname again.
        module_stat = module["stat"]
        maps = (proc / "maps").read_bytes()
        require(0 < len(maps) <= MAX_MAPS, "actual_native_process_maps_unbounded")
        attestation = parse_maps(
            maps.decode("utf-8"), str(module_path),
            (os.major(module_stat.st_dev), os.minor(module_stat.st_dev)),
            module_stat.st_ino,
        )
        cmdline = verified_melt_cmdline(
            (proc / "cmdline").read_bytes(), expected_output_path,
            **({"relative_cwd": cwd} if host_sealed_melt else {}),
        )
        env = verified_restricted_environment((proc / "environ").read_bytes())
        second_start = parse_process_starttime((proc / "stat").read_bytes())
        require(second_start == process_start,
                "live_native_MLT_PID_changed_during_observation")
        require(os.readlink(proc / "exe") == actual_melt,
                "live_native_melt_executable_changed_during_observation")
        if host_sealed_melt:
            after_cwd = (proc / "cwd").stat()
            require(os.readlink(proc / "cwd") == cwd_link
                    and (after_cwd.st_dev, after_cwd.st_ino)
                        == (cwd_identity.st_dev, cwd_identity.st_ino),
                    "host_sealed_melt_cwd_changed_during_observation")
        # Both descriptors remain open until the final generation checks on
        # context exit. A changed file prevents this receipt from returning.
        return {
            "schema": "motionwright.native.mlt-loaded-consumer/1",
            "actual_live_melt_pid": pid,
            **({"actual_process_executable_scope": "host-sealed-melt",
                "actual_process_executable_namespace_path": "/plugin/tools/melt",
                "actual_process_executable_device": exe["stat"].st_dev,
                "actual_process_executable_inode": exe["stat"].st_ino,
                "actual_process_cwd": cwd,
                "actual_process_cwd_device": cwd_identity.st_dev,
                "actual_process_cwd_inode": cwd_identity.st_ino}
               if host_sealed_melt else {}),
            "original_melt_sha256": exe["sha256"],
            "original_repaired_avformat_module_sha256": module["sha256"],
            "source_compiler_module_sha256_must_be_attested_separately": True,
            "mapped_native_module": attestation,
            "source_bound_native_consumer_argv": cmdline,
            "native_loader_environment": env,
            "module_installed_before_original_effect": False,
            "module_installation_order_requires_separate_evidence": True,
            "original_runtime_loaded_module_attested": True,
            "native_av_master_decoded_right_tail_verified": False,
            "human_audio_acceptance": False,
        }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pid", type=int, required=True)
    p.add_argument("--melt", type=Path, required=True)
    p.add_argument("--melt-sha256", required=True)
    p.add_argument("--module", type=Path, required=True)
    p.add_argument("--module-sha256", required=True)
    p.add_argument("--output-absolute", required=True)
    p.add_argument("--receipt", type=Path, required=True)
    args = p.parse_args()
    require(args.receipt.is_absolute() and args.receipt.parent.is_dir()
            and not args.receipt.exists() and not args.receipt.is_symlink(),
            "private_loaded_module_receipt_path_not_fresh")
    value = inspect_live_original(
        args.pid, args.melt, args.melt_sha256,
        args.module, args.module_sha256, args.output_absolute,
    )
    with args.receipt.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    main()
