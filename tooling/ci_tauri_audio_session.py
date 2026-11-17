#!/usr/bin/env python3
"""Own one disposable window manager around the real Tauri audio harness.

The window manager is a sibling of the harness, never part of its writer tree.
A wrapper subreaper retains orphan custody if the harness itself fails.
Only kernel-confirmed owned children are signalled during final cleanup.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

import ci_tauri_audio_audition as gate
import native_host_installed_shared_state as processes

ROOT = Path(__file__).resolve().parents[1]

def resource_limit():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--desktop", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--build-original", type=Path, required=True)
    parser.add_argument("--seconds", type=int, choices=(120,), required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    current = gate.context(args.repo, os.environ.get("MW_TAURI_AUDIO_EXPECTED_SOURCE", ""))
    gate.need(args.repo == ROOT and os.environ.get("DISPLAY")
              and os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "OWNED_X11_DBUS_SESSION_REQUIRED")
    output, _ = gate.locations(os.environ)
    admission = gate.strict(gate.regular(output / "admission.original.json", 65536))
    gate.need(admission["context"] == current, "WINDOW_MANAGER_SOURCE_CONTEXT_CHANGED")
    directory = output / "window-manager"
    directory.mkdir(mode=0o700)
    environment = dict(os.environ)
    for key, name in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                      ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                      ("XDG_RUNTIME_DIR", "runtime"), ("TMPDIR", "tmp")):
        path = directory / name
        path.mkdir(mode=0o700)
        environment[key] = str(path)
    environment["GDK_BACKEND"] = "x11"
    # The existing distro default provides real WM close/focus behavior.
    # No fixture control is substituted for the application's window.
    config_source = Path("/etc/xdg/openbox/rc.xml")
    config = config_source.read_bytes()
    gate.need(0 < len(config) <= 256 * 1024, "WINDOW_MANAGER_CONFIG_BOUND")
    configuration = ET.fromstring(config)
    bindings = [node for node in configuration.iter()
                if node.tag.rsplit("}", 1)[-1] == "keybind" and node.get("key") == "A-F4"]
    gate.need(len(bindings) == 1 and len(list(bindings[0])) == 1
              and list(bindings[0])[0].tag.rsplit("}", 1)[-1] == "action"
              and list(bindings[0])[0].get("name") == "Close",
              "REAL_WINDOW_MANAGER_CLOSE_BINDING_REQUIRED")
    config_path = directory / "rc.original.xml"
    gate.publish_bytes(config_path, config)
    gate.publish(directory / "intent.original.json", {
        "schema": "motionwright.tauri-audio-window-manager/1", "context": current,
        "source_config": str(config_source), "config_sha256": gate.sha(config),
        "argv": ["/usr/bin/openbox", "--sm-disable", "--config-file", str(config_path)],
        "automatic_retry": False, "formal_G2": False, "formal_G3": False,
    })
    result = {"schema": "motionwright.tauri-audio-window-manager/1", "context": current,
              "status": "FAIL", "supervisor_pid": os.getpid(),
              "harness_exit_code": None, "owned_window_manager_closed": False,
              "termination_requested": False, "forced_kill_requested": False,
              "automatic_retry": False, "formal_G2": False, "formal_G3": False}
    manager, closure = None, None
    with processes.supervise_own_processes():
        try:
            with (directory / "stdout.original").open("xb") as out, (directory / "stderr.original").open("xb") as err:
                manager = subprocess.Popen(
                    ["/usr/bin/openbox", "--sm-disable", "--config-file", str(config_path)],
                    cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                    stdout=out, stderr=err, start_new_session=True, preexec_fn=resource_limit,
                )
                result["window_manager_pid"] = manager.pid
                gate.need(manager.poll() is None, "WINDOW_MANAGER_EXITED_BEFORE_HARNESS")
                command = [
                    sys.executable, "-B", "-E", "-s", str(ROOT / "tooling/native_tauri_audio_audition.py"),
                    "--repo", str(args.repo), "--desktop", str(args.desktop),
                    "--fixture", str(args.fixture), "--build-original", str(args.build_original),
                    "--seconds", str(args.seconds),
                ]
                gate.publish(directory / "harness-intent.original.json", {
                    "argv": command, "maximum_seconds": 360, "automatic_retry": False,
                })
                child = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       timeout=360, check=False)
                result["harness_exit_code"] = child.returncode
                gate.need(child.returncode == 0, "ORIGINAL_AUDIO_HARNESS_FAILED")
        except BaseException as error:
            result["failure_type"] = type(error).__name__
            result["failure_code"] = str(error) if isinstance(error, gate.Rejected) else type(error).__name__
        finally:
            try:
                prior_exit = manager.poll() if manager is not None else None
                # This retained implementation uses waitid(P_PIDFD) before
                # signalling, and reaps only verified adopted kernel children.
                closure = processes.close_own_children()
                closure["supervisor_pid"] = os.getpid()
                gate.publish(directory / "wrapper-process-closure.original.json", closure)
                gate.need(gate.closed_writer_observed(closure), "WRAPPER_DESCENDANTS_NOT_CLOSED")
                if manager is not None:
                    signals = [row["signal"] for row in closure["signals"] if row["pid"] == manager.pid]
                    reaped = [row for row in closure["reaped"] if row["pid"] == manager.pid]
                    result["termination_requested"] = int(signal.SIGTERM) in signals
                    result["forced_kill_requested"] = int(signal.SIGKILL) in signals
                    result["window_manager_exit_code"] = (
                        os.waitstatus_to_exitcode(reaped[0]["wait_status"]) if len(reaped) == 1 else prior_exit)
                    result["owned_window_manager_closed"] = (
                        len(reaped) == 1 or prior_exit is not None)
                    # Popen may observe ECHILD after the subreaper's exact wait.
                    # Its synthetic returncode is never substituted for the
                    # original kernel wait status recorded immediately above.
                    manager.poll()
                if result["harness_exit_code"] == 0 and result["owned_window_manager_closed"] and not result["forced_kill_requested"]:
                    result["status"] = "PASS"
            except BaseException as error:
                result["status"] = "FAIL"
                result["closure_failure_type"] = type(error).__name__
    gate.publish(directory / "completion.original.json", result)
    print(json.dumps({"window_manager_wrapper": result["status"],
                      "automatic_retry": False, "formal_G2": False, "formal_G3": False}))
    return 0 if result["status"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
