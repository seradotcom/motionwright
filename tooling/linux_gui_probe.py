#!/usr/bin/env python3
"""Rootless, disposable Xvfb launch probe for the exact extracted AppImage.

This only proves a matching native window appeared in an automated Linux
runner. It does not prove render functionality, installation on a real user's
device, signing, accessibility or human quality acceptance.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Callable


def check_window_output(output: str) -> bool:
    """One exact original X11 client title; never a log or partial title.

    The first-use caller may perform one real GUI action after this predicate.
    It must agree with the strict single-window guard in linux_cold_start.
    """
    if not isinstance(output, str) or len(output) > 262144:
        return False
    matches = re.findall(
        r'^\s*(0x[0-9a-fA-F]+)\s+"Motionwright":', output, re.MULTILINE,
    )
    return len(matches) == 1


def _proc_group_and_start(raw: bytes, pid: int) -> tuple[int, int]:
    """Bind a PID to its kernel process group and non-reused start time."""
    if not 0 < len(raw) <= 4096:
        raise ValueError("proc_stat_byte_budget")
    end = raw.rfind(b") ")
    if end < 0 or raw.split(b" (", 1)[0] != str(pid).encode("ascii"):
        raise ValueError("proc_stat_identity")
    fields = raw[end + 2:].split()
    if len(fields) < 20:
        raise ValueError("proc_stat_fields")
    group, started = int(fields[2]), int(fields[19])
    if group <= 0 or started <= 0:
        raise ValueError("proc_stat_invalid")
    return group, started


def observe_runtime_loader(
    launcher_pid: int, app_dir: Path, *, proc_root: Path = Path("/proc"),
    link_reader: Callable[[Path], str] | None = None,
) -> dict:
    """Sample one real installed executable's mapped files while its window lives.

    A single /proc/maps snapshot is neither a full dlopen lifetime nor a file
    content hash, license grant, package-ownership proof or G3 acceptance.
    Outside a Linux production call, only synthetic source tests use proc_root.
    """
    report = {
        "schema": "motionwright.linux-installed-loader-path-snapshot/1",
        "status": "NOT_OBSERVED",
        "source": "WINDOW_SCOPED_PROC_MAPS",
        "claims": {
            "G3": "STOP", "host_package_ownership": "NOT_ATTESTED",
            "mapped_file_bytes": "NOT_AUTHENTICATED",
            "all_dlopen_events": "NOT_OBSERVED",
            "human_acceptance": False,
        },
    }
    def unavailable(reason: str) -> dict:
        return {**report, "reason": reason}

    if type(launcher_pid) is not int or launcher_pid <= 1 or launcher_pid > 2**31:
        return unavailable("INVALID_LAUNCHER_PID")
    if proc_root == Path("/proc") and sys.platform != "linux":
        return unavailable("LINUX_ONLY")
    try:
        root = app_dir.resolve(strict=True)
        expected = root / "usr/bin/motionwright-desktop"
        if not expected.is_file() or expected.is_symlink():
            return unavailable("INSTALLED_EXECUTABLE_MISSING")
        expected = expected.resolve(strict=True)
        readlink = os.readlink if link_reader is None else link_reader
        matched = []
        inspected = 0
        for child in proc_root.iterdir():
            if not child.name.isdecimal():
                continue
            inspected += 1
            if inspected > 4096:
                return unavailable("PROC_ROSTER_BUDGET")
            pid = int(child.name)
            if pid <= 1:
                continue
            try:
                with (child / "stat").open("rb") as stream:
                    before = _proc_group_and_start(stream.read(4097), pid)
                if before[0] != launcher_pid or readlink(child / "exe") != str(expected):
                    continue
                with (child / "maps").open("rb") as stream:
                    raw = stream.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    return unavailable("PROC_MAPS_BYTE_BUDGET")
                with (child / "stat").open("rb") as stream:
                    after = _proc_group_and_start(stream.read(4097), pid)
                if before != after or readlink(child / "exe") != str(expected):
                    return unavailable("PROC_IDENTITY_CHANGED")
                matched.append((pid, before[1], raw))
            except (OSError, ValueError):
                # A disappearing process or restricted proc entry is not proof.
                continue
        if len(matched) != 1:
            return unavailable("AUTHENTICATED_PROCESS_ABSENT_OR_AMBIGUOUS")
        pid, start, raw = matched[0]
        paths = {}
        unclassified = 0
        lines = raw.decode("utf-8", "strict").splitlines()
        if len(lines) > 8192:
            return unavailable("PROC_MAPS_ROW_BUDGET")
        for line in lines:
            fields = line.split(maxsplit=5)
            if len(fields) != 6 or "x" not in fields[1]:
                continue
            _, perms, _, device, inode_text, mapped = fields
            if not re.fullmatch(r"[0-9a-fA-F]+:[0-9a-fA-F]+", device) or not inode_text.isdecimal():
                continue
            inode = int(inode_text)
            if inode <= 0 or mapped.endswith(" (deleted)"):
                unclassified += 1
                continue
            if mapped.startswith(("/usr/lib/", "/lib/")):
                kind, safe_path = "HOST_SYSTEM", mapped
            else:
                path = Path(mapped)
                if not path.is_relative_to(root):
                    unclassified += 1
                    continue
                kind, safe_path = "BUNDLED", path.relative_to(root).as_posix()
            key = (kind, safe_path, device.lower(), inode)
            paths[key] = {"origin_class": kind, "path": safe_path,
                          "mapped_device": device.lower(), "mapped_inode": inode}
            if len(paths) > 512:
                return unavailable("PROC_MAPS_FILE_BUDGET")
        if not any(row["origin_class"] == "BUNDLED"
                   and row["path"] == "usr/bin/motionwright-desktop"
                   for row in paths.values()):
            return unavailable("EXECUTABLE_MAP_NOT_WITNESSED")
        return {
            **report, "status": "LIVE_EXECUTABLE_MAPPINGS_OBSERVED",
            "process_pid": pid, "process_starttime_ticks": start,
            "mapped_files": [paths[key] for key in sorted(paths)],
            "unclassified_executable_segments": unclassified,
        }
    except (OSError, ValueError, UnicodeDecodeError):
        return unavailable("PROC_OR_INSTALLED_NAMESPACE_UNAVAILABLE")


def run_probe(
    app_run: Path, scratch: Path, *, seconds: int,
    ready_check: Callable[[Path], bool] | None = None,
    window_interaction: Callable[[Path], bool] | None = None,
    loader_observer: Callable[[int, Path], dict] | None = None,
) -> dict:
    app_dir = app_run.parent.resolve(strict=True)
    resolved = app_run.resolve(strict=True)
    if not resolved.is_relative_to(app_dir) or not resolved.is_file():
        raise RuntimeError("AppImage AppRun must resolve to a regular file inside its extracted bundle")
    if "DISPLAY" not in os.environ or not os.environ["DISPLAY"]:
        raise RuntimeError("GUI smoke requires a real Xvfb DISPLAY")
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for label, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        path = scratch / folder
        path.mkdir(mode=0o700, exist_ok=True)
        env[label] = str(path)
    # No owner-provisioned Semwright runtime or connection can leak into the
    # disposable GUI smoke process, even if the runner inherited one.
    env.pop("MOTIONWRIGHT_SEMWRIGHT_CONNECTION", None)
    env["APPDIR"] = str(app_dir)
    env["GDK_BACKEND"] = "x11"
    log_file = scratch / "app-run.log"
    result = {"status": "NO_WINDOW", "window_title": "", "elapsed_seconds": 0}
    window_seen = False
    interacted = window_interaction is None
    with log_file.open("wb") as log:
        child = subprocess.Popen(
            [str(app_run)], cwd=app_run.parent,
            env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        start = time.monotonic()
        try:
            while time.monotonic() - start < seconds:
                if child.poll() is not None:
                    raise RuntimeError(
                        f"Candidate process exited before creating a window: exit={child.returncode}"
                    )
                check = subprocess.run(
                    ["xwininfo", "-root", "-tree"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=5, check=False,
                )
                if check.returncode == 0 and check_window_output(check.stdout):
                    window_seen = True
                    # The first-use handler may inspect readiness repeatedly,
                    # but it must dispatch a GUI mutation at most once.
                    if not interacted:
                        interacted = bool(window_interaction(scratch))
                        time.sleep(0.6)
                        continue
                    # Tauri can expose a window before its setup() callback
                    # finishes creating the local SQLite project. The cold
                    # relaunch test requires both a live window and committed
                    # state before terminating the app. The ordinary window
                    # smoke still uses its original immediate behavior.
                    if ready_check is not None and not ready_check(scratch):
                        time.sleep(0.6)
                        continue
                    result = {
                        "status": "WINDOW_OBSERVED",
                        "window_title": "Motionwright",
                        "elapsed_seconds": round(time.monotonic() - start, 2),
                    }
                    if loader_observer is not None:
                        loader = loader_observer(child.pid, app_dir)
                        if (not isinstance(loader, dict) or
                                loader.get("claims", {}).get("G3") != "STOP"):
                            raise RuntimeError("Installed loader observation cannot grant G3")
                        result["loader_observation"] = loader
                    break
                time.sleep(0.6)
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=4)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=4)
    if result["status"] != "WINDOW_OBSERVED":
        if window_seen and ready_check is not None:
            if window_interaction is not None:
                if not interacted:
                    raise RuntimeError(
                        "Installed first-use empty-store authority never permitted a native UI action"
                    )
                raise RuntimeError(
                    "One original native Create action was sent but SQLite never confirmed a project"
                )
            raise RuntimeError("Window appeared, but installed project bootstrap never became ready within the bounded probe")
        raise RuntimeError("Motionwright never created an X11 window within the bounded probe")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app-run", required=True, type=Path)
    p.add_argument("--scratch", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--seconds", type=int, default=24)
    args = p.parse_args()
    if not (2 <= args.seconds <= 45):
        p.error("GUI probing must be limited to 2–45 seconds")
    result = run_probe(args.app_run, args.scratch, seconds=args.seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, sort_keys=True)
        out.write("\n")
    print(f"candidate-gui-smoke={result['status']} elapsed={result['elapsed_seconds']}s "
          "human_install_acceptance=NOT_RUN")


if __name__ == "__main__":
    main()
