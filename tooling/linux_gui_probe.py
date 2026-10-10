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
import time


def check_window_output(output: str) -> bool:
    # xwininfo root tree reports the actual X11 client window title.
    # Case- and quote-sensitive to avoid treating log text as window evidence.
    return bool(re.search(r'\bMotionwright\b', output))


def run_probe(app_run: Path, scratch: Path, *, seconds: int) -> dict:
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
                    result = {
                        "status": "WINDOW_OBSERVED",
                        "window_title": "Motionwright",
                        "elapsed_seconds": round(time.monotonic() - start, 2),
                    }
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
