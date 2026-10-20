#!/usr/bin/env python3
"""Attest a live native Kdenlive executable and a separate project reopen.

Read-only Linux /proc witness for an authorized external-editor integration
test. It does not launch the editor, press UI controls, prove an OTIO import,
claim a native export, modify a project or approve creative work. A complete
application test also needs independent UI/save/export receipts and original
source-media/frame/loss verification before any product acceptance.

The original imported OTIO is bound by source SHA-256. At reopen, the new
Kdenlive process must have received the saved .kdenlive path as an argument;
a different PID/starttime is required to distinguish a real new process
from reusing a stale open project tab in the prior process. The reopened input
is hashed separately from a later edited output, and must match the first
process\'s saved path and digest before a cross-process reopen is admitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

HEX = re.compile(r"[a-f0-9]{64}\Z")
MAX_NATIVE_BYTES = 5 * 1024 * 1024
MAX_OTIO_BYTES = 2 * 1024 * 1024
MAX_EXE_BYTES = 128 * 1024 * 1024
MAX_CMDLINE_BYTES = 16 * 1024
MAX_STAT_BYTES = 8192
APP_NAMES = frozenset({"kdenlive", "kdenlive-bin"})


class NativeKdenliveProcessRejected(ValueError):
    """No authentic, source-bound native application process was observed."""


def require(ok: object, reason: str) -> None:
    if not ok:
        raise NativeKdenliveProcessRejected(reason)


def read_exact_file(path: Path, expected_sha256: str, budget: int) -> dict:
    require(type(expected_sha256) is str and HEX.fullmatch(expected_sha256)
            and path.is_absolute() and path.is_file()
            and not path.is_symlink()
            and not any(parent.is_symlink() for parent in path.parents),
            "native_Kdenlive_source_path_or_expected_SHA_not_admitted")
    before = path.stat()
    require(0 < before.st_size <= budget,
            "native_Kdenlive_source_file_outside_byte_budget")
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    after = path.stat()
    require((before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns)
            and hasher.hexdigest() == expected_sha256,
            "native_Kdenlive_source_changed_during_process_attestation")
    return {
        "sha256": expected_sha256,
        "bytes": before.st_size,
        "inode": before.st_ino,
    }


def parse_native_kdenlive_version(raw: bytes) -> str:
    """Kdenlive 25.04.3 fixed OTIO export track ordering.

    This native G2 exact-order profile requires 25.04.3 or a later release.
    Version text alone never proves the actual app process or UI export.
    """
    require(0 < len(raw) <= 2048,
            "native_Kdenlive_version_stdout_missing_or_unbounded")
    try:
        text = raw.decode("ascii").strip()
    except UnicodeError as error:
        raise NativeKdenliveProcessRejected(
            "native_Kdenlive_version_stdout_not_ASCII"
        ) from error
    match = re.search(
        r"\bkdenlive\s+(?:version\s+)?v?(\d{2})\.(\d{1,2})\.(\d+)(?![0-9])",
        text, re.IGNORECASE,
    )
    require(match is not None,
            "native_Kdenlive_original_application_version_unidentified")
    numbers = tuple(map(int, match.groups()))
    require(numbers >= (25, 4, 3),
            "native_Kdenlive_OTIO_export_version_predates_track_order_fix")
    return ".".join(str(x) for x in numbers)


def inspect_actual_kdenlive_version(executable: Path) -> str:
    """Separate read-only --version invocation of the same SHA-bound binary."""
    require(executable.is_absolute() and executable.is_file()
            and executable.name in APP_NAMES,
            "native_Kdenlive_version_command_not_original_binary")
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True, timeout=15,
            stdin=subprocess.DEVNULL, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NativeKdenliveProcessRejected(
            "native_Kdenlive_original_application_version_not_observable"
        ) from error
    require(result.returncode == 0
            and len(result.stdout) <= 2048
            and len(result.stderr) <= 4096,
            "native_Kdenlive_version_command_failed_or_unbounded")
    return parse_native_kdenlive_version(result.stdout)


def process_starttime(raw: bytes, expected_pid: int) -> int:
    """Read /proc/<pid>/stat field 22 even with parentheses in comm."""
    require(type(expected_pid) is int and 1 < expected_pid < 2**30
            and 0 < len(raw) <= MAX_STAT_BYTES,
            "native_Kdenlive_process_pid_or_stat_outside_budget")
    try:
        payload = raw.decode("ascii").strip()
        prefix, tail = payload.rsplit(") ", 1)
        pid_part, command = prefix.split(" (", 1)
        words = tail.split()
        require(pid_part == str(expected_pid)
                and command and len(words) >= 20,
                "native_Kdenlive_stat_not_its_original_pid")
        value = int(words[19])
    except NativeKdenliveProcessRejected:
        raise
    except (UnicodeError, ValueError, IndexError) as error:
        raise NativeKdenliveProcessRejected(
            "native_Kdenlive_stat_malformed_or_truncated"
        ) from error
    require(value > 0,
            "native_Kdenlive_starttime_not_monotone_kernel_ticks")
    return value


def check_commandline(raw: bytes, exe: Path, phase: str,
                      native_file: Path) -> dict:
    require(0 < len(raw) <= MAX_CMDLINE_BYTES and raw.endswith(b"\0"),
            "native_Kdenlive_process_arguments_missing_or_unbounded")
    try:
        arguments = [arg.decode("utf-8") for arg in raw[:-1].split(b"\0")]
    except UnicodeDecodeError as error:
        raise NativeKdenliveProcessRejected(
            "native_Kdenlive_process_arguments_not_UTF8"
        ) from error
    require(phase in ("import", "reopen") and arguments
            and Path(arguments[0]).name in APP_NAMES
            and exe.name in APP_NAMES,
            "native_Kdenlive_executable_or_source_phase_does_not_match")
    exact_native_argument = (
        str(native_file) in arguments
        or native_file.as_uri() in arguments
    )
    if phase == "reopen":
        require(exact_native_argument,
                "native_Kdenlive_new_process_was_not_launched_with_saved_project")
    return {
        "bounded_arg_count": len(arguments),
        "saved_project_was_an_exact_launch_argument": exact_native_argument,
        "native_project_path_checked": str(native_file),
        "native_process_phase": phase,
        "no_ambiguous_project_basename_substring_matching": True,
    }


def inspect_live_native_kdenlive(
    pid: int,
    expected_binary: Path,
    expected_binary_sha256: str,
    original_otio: Path,
    original_otio_sha256: str,
    saved_native: Path,
    saved_native_sha256: str,
    *,
    phase: str,
    reopen_input: Path | None = None,
    reopen_input_sha256: str | None = None,
) -> dict:
    require(sys.platform == "linux"
            and os.environ.get("CI") == "true"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE") == "1",
            "native_Kdenlive_live_process_requires_selected_Linux_GHA")
    require(type(pid) is int and 1 < pid < 2**30
            and expected_binary.is_absolute()
            and expected_binary.name in APP_NAMES,
            "native_Kdenlive_live_pid_or_executable_not_admitted")
    require(phase in ("import", "reopen")
            and (reopen_input is None) == (reopen_input_sha256 is None)
            and (phase == "reopen" or reopen_input is None)
            and (reopen_input is None or isinstance(reopen_input, Path)),
            "native_Kdenlive_reopen_input_arguments_not_admitted")
    binary = read_exact_file(expected_binary, expected_binary_sha256, MAX_EXE_BYTES)
    version = inspect_actual_kdenlive_version(expected_binary)
    source = read_exact_file(original_otio, original_otio_sha256, MAX_OTIO_BYTES)
    require(saved_native.suffix.lower() == ".kdenlive",
            "native_Kdenlive_saved_file_extension_not_admitted")
    native = read_exact_file(saved_native, saved_native_sha256, MAX_NATIVE_BYTES)
    input_path = saved_native if reopen_input is None else reopen_input
    input_sha256 = (saved_native_sha256 if reopen_input_sha256 is None
                    else reopen_input_sha256)
    reopened_input = None
    if phase == "reopen":
        require(input_path.suffix.lower() == ".kdenlive",
                "native_Kdenlive_reopen_input_extension_not_admitted")
        # Read original input bytes independently of a later Save As output.
        # An in-place edit that destroys these bytes cannot attest the input.
        reopened_input = read_exact_file(
            input_path, input_sha256, MAX_NATIVE_BYTES,
        )
    proc = Path("/proc") / str(pid)
    require(proc.is_dir(), "native_Kdenlive_application_process_not_running")
    first_start = process_starttime((proc / "stat").read_bytes(), pid)
    try:
        running_exe = os.readlink(proc / "exe")
        cmdline = (proc / "cmdline").read_bytes()
    except (OSError, ProcessLookupError) as error:
        raise NativeKdenliveProcessRejected(
            "native_Kdenlive_process_exited_during_inspection"
        ) from error
    require(running_exe == str(expected_binary.resolve(strict=True)),
            "live_native_Kdenlive_executable_or_unauthorized_binary_changed")
    args = check_commandline(cmdline, expected_binary, phase, input_path)
    second_start = process_starttime((proc / "stat").read_bytes(), pid)
    require(first_start == second_start
            and os.readlink(proc / "exe") == running_exe,
            "native_Kdenlive_PID_reused_or_executable_changed")
    for path, sha, budget in (
        (expected_binary, expected_binary_sha256, MAX_EXE_BYTES),
        (original_otio, original_otio_sha256, MAX_OTIO_BYTES),
        (saved_native, saved_native_sha256, MAX_NATIVE_BYTES),
    ):
        read_exact_file(path, sha, budget)
    if phase == "reopen":
        read_exact_file(input_path, input_sha256, MAX_NATIVE_BYTES)
    return {
        "schema": "motionwright.native.kdenlive-live-process/1",
        "phase": phase,
        "pid": pid,
        "kernel_starttime_ticks": first_start,
        "actual_executable_sha256": binary["sha256"],
        "original_Kdenlive_application_version": version,
        "native_OTIO_export_order_fix_version_admitted": True,
        "source_original_OTIO_sha256": source["sha256"],
        "source_original_OTIO_bytes": source["bytes"],
        "saved_native_project_path": str(saved_native),
        "saved_native_project_sha256": native["sha256"],
        "saved_native_project_bytes": native["bytes"],
        "reopen_input_project_path": (
            str(input_path) if reopened_input is not None else None
        ),
        "reopen_input_project_sha256": (
            reopened_input["sha256"] if reopened_input is not None else None
        ),
        "process_arguments": args,
        "live_application_binary_and_pid_verified": True,
        "original_otio_import_UI_action_authenticated": False,
        "native_save_or_export_UI_action_authenticated": False,
        "separate_reopen_application_verified_by_this_one_snapshot": False,
        "human_media_rights_or_perceptual_review": False,
    }


def compare_distinct_native_sessions(first: dict, reopened: dict) -> dict:
    require(type(first) is dict and type(reopened) is dict
            and first.get("schema") == reopened.get("schema") ==
            "motionwright.native.kdenlive-live-process/1"
            and first.get("phase") == "import"
            and reopened.get("phase") == "reopen",
            "native_Kdenlive_session_phases_not_original_and_reopen")
    require(first.get("live_application_binary_and_pid_verified") is True
            and reopened.get("live_application_binary_and_pid_verified") is True
            and first.get("actual_executable_sha256") ==
            reopened.get("actual_executable_sha256")
            and first.get("original_Kdenlive_application_version") ==
            reopened.get("original_Kdenlive_application_version")
            and first.get("source_original_OTIO_sha256") ==
            reopened.get("source_original_OTIO_sha256")
            and type(first.get("pid")) is int
            and type(reopened.get("pid")) is int
            and type(first.get("kernel_starttime_ticks")) is int
            and type(reopened.get("kernel_starttime_ticks")) is int,
            "native_Kdenlive_edited_process_not_original_executable_or_source")
    require((first["pid"], first["kernel_starttime_ticks"])
            != (reopened["pid"], reopened["kernel_starttime_ticks"])
            and reopened["kernel_starttime_ticks"] >
            first["kernel_starttime_ticks"]
            and type(reopened.get("process_arguments")) is dict
            and reopened.get("process_arguments", {})
            .get("saved_project_was_an_exact_launch_argument") is True,
            "native_Kdenlive_reopen_did_not_launch_newer_distinct_process")
    original_path = first.get("saved_native_project_path")
    original_sha256 = first.get("saved_native_project_sha256")
    reopened_output_path = reopened.get("saved_native_project_path")
    require(type(original_path) is str
            and PurePosixPath(original_path).is_absolute()
            and ".." not in PurePosixPath(original_path).parts
            and PurePosixPath(original_path).suffix.lower() == ".kdenlive",
            "native_Kdenlive_original_saved_project_identity_missing_or_invalid")
    require(type(reopened_output_path) is str
            and PurePosixPath(reopened_output_path).is_absolute()
            and ".." not in PurePosixPath(reopened_output_path).parts
            and PurePosixPath(reopened_output_path).suffix.lower() == ".kdenlive",
            "native_Kdenlive_reopened_output_identity_missing_or_invalid")
    require(reopened.get("reopen_input_project_path") == original_path
            and reopened["process_arguments"].get("native_project_path_checked")
            == original_path,
            "native_Kdenlive_reopen_input_path_does_not_match_original_saved_project")
    require(type(original_sha256) is str and HEX.fullmatch(original_sha256)
            and reopened.get("reopen_input_project_sha256") == original_sha256,
            "native_Kdenlive_reopen_input_SHA_does_not_match_original_saved_project")
    return {
        "schema": "motionwright.native.kdenlive-distinct-process-reopen/1",
        "original_OTIO_sha256": first["source_original_OTIO_sha256"],
        "native_executable_sha256": first["actual_executable_sha256"],
        "native_Kdenlive_version": first["original_Kdenlive_application_version"],
        "initial_native_project_path": original_path,
        "initial_native_project_sha256": original_sha256,
        "reopen_input_project_path": reopened["reopen_input_project_path"],
        "reopen_input_project_sha256": reopened["reopen_input_project_sha256"],
        "reopened_native_project_path": reopened_output_path,
        "reopened_native_project_sha256": reopened["saved_native_project_sha256"],
        "two_distinct_live_native_process_snapshots": True,
        "reopened_process_received_original_saved_path": True,
        "native_GUI_OTIO_import_or_export_verified_here": False,
        "separate_native_XML_semantics_validation_required": True,
        "human_acceptance": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--binary-sha256", required=True)
    parser.add_argument("--otio", type=Path, required=True)
    parser.add_argument("--otio-sha256", required=True)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--native-sha256", required=True)
    parser.add_argument("--phase", choices=("import", "reopen"), required=True)
    parser.add_argument(
        "--reopen-input", type=Path,
        help="Original saved project passed to the reopened process; defaults to --native",
    )
    parser.add_argument(
        "--reopen-input-sha256",
        help="SHA-256 of original reopen input; required with --reopen-input",
    )
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    require(args.receipt.is_absolute()
            and args.receipt.parent.is_dir()
            and not args.receipt.exists()
            and not args.receipt.is_symlink(),
            "native_Kdenlive_process_receipt_destination_not_new")
    payload = inspect_live_native_kdenlive(
        args.pid, args.binary, args.binary_sha256,
        args.otio, args.otio_sha256,
        args.native, args.native_sha256, phase=args.phase,
        reopen_input=args.reopen_input,
        reopen_input_sha256=args.reopen_input_sha256,
    )
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with args.receipt.open("x", encoding="utf-8") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    print(json.dumps({
        "process_phase": payload["phase"],
        "native_binary_SHA256": payload["actual_executable_sha256"],
        "actual_live_pid_attested": True,
        "real_GUI_import_or_export_action_verified": False,
        "human_acceptance": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
