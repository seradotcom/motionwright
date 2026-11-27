"""Observe the first native Kdenlive save of the hosted synthetic OTIO cut."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from kdenlive_native_distribution import JOB, ROOT, publish, selection
from kdenlive_native_installation import windows_accessibility_context
from kdenlive_native_source import MAX_METADATA_BYTES, admitted_source, inspect_initial_save
from kdenlive_readback import MAX_XML_BYTES
from source_checkout_observation import git, regular_file, require
from windows_updated_application import process_identity


def run_windows(runner, context, distribution):
    require(context["runner_os"] == "Windows"
            and runner.is_absolute() and runner.resolve(strict=True) == runner,
            "Windows editor run requires the canonical hosted root")
    accessibility = windows_accessibility_context()
    require(all(accessibility.values()), "interactive Windows desktop unavailable")
    owner = runner / f"{JOB}-{context['run_id']}-1"
    editor, binary, source = admitted_source(owner, context, distribution)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    regular_file(powershell, 16 * 1024 * 1024)
    directory = owner / "windows-editor"
    directory.mkdir(mode=0o700)
    profile = directory / "profile"
    profile.mkdir()
    (profile / "roaming").mkdir()
    (profile / "local").mkdir()
    environment = {**os.environ, "APPDATA": str(profile / "roaming"),
                   "LOCALAPPDATA": str(profile / "local"), "LANG": "en_US.UTF-8",
                   "LANGUAGE": "en_US", "LC_ALL": "en_US.UTF-8", "QT_ACCESSIBILITY": "1"}
    publish(directory / "launch-intent.json", {
        "context": context, "executable": binary, "timeline": source["timeline"],
        "editor_launch_count": 1, "automatic_retry": False,
    })
    process = None
    terminal = {"status": "IMPORT_NOT_DISPATCHED", "automatic_retry": False, "process": None}
    try:
        with (directory / "application.stdout.log").open("xb") as app_out, (
                directory / "application.stderr.log").open("xb") as app_err:
            process = subprocess.Popen(
                [str(editor), "--no-welcome", "--config", str(profile / "kdenliverc")],
                cwd=editor.parent, env=environment, stdin=subprocess.DEVNULL,
                stdout=app_out, stderr=app_err,
            )
            identity = process_identity(process, editor)
            terminal["process"] = identity
            with (directory / "observer.stdout.log").open("xb") as obs_out, (
                    directory / "observer.stderr.log").open("xb") as obs_err:
                result = subprocess.run(
                    [str(powershell), "-NoProfile", "-NonInteractive", "-File",
                     str(Path(__file__).with_suffix(".ps1")),
                     "-EditorPid", str(identity["pid"]),
                     "-EditorCreationFiletime", str(identity["creation_filetime"]),
                     "-Executable", str(editor), "-OriginalOtio", source["timeline"]["path"],
                     "-OriginalSha256", source["timeline"]["sha256"], "-Evidence", str(directory)],
                    cwd=directory, stdin=subprocess.DEVNULL, stdout=obs_out, stderr=obs_err, timeout=160,
                )
            require(result.returncode == 0, f"Kdenlive window observer failed ({result.returncode})")
            terminal["status"] = "UNKNOWN"
            require(process.poll() is None and process_identity(process, editor) == identity
                    and admitted_source(owner, context, distribution) == (editor, binary, source),
                    "Kdenlive process, executable or source changed during import")
            saved = directory / "initial.kdenlive"
            observed = regular_file(saved, MAX_XML_BYTES)
            acknowledgment = directory / "initial-save-observed.json"
            ack_original = regular_file(acknowledgment, MAX_METADATA_BYTES)
            raw = acknowledgment.read_bytes()
            require(sha256(raw).hexdigest() == ack_original["sha256"],
                    "window observer acknowledgment changed during read")
            ack = json.loads(raw)
            require(ack == {"editor_pid": identity["pid"], "source_sha256": source["timeline"]["sha256"],
                            "saved_sha256": observed["sha256"]},
                    "saved Kdenlive project differs from the window observer")
            terminal["status"] = "INITIAL_SAVE_OBSERVED"
            readback = inspect_initial_save(
                saved, observed["sha256"], source["manifest"]["sha256"], owner / "otio-original",
            )
            require(regular_file(saved, MAX_XML_BYTES) == observed,
                    "native Kdenlive project changed during readback")
            publish(directory / "readback.json", {
                "context": context, "process": identity, "initial_saved_project": observed,
                "source": source, "readback": readback,
            })
            terminal["status"] = "INITIAL_SAVE_VERIFIED"
    finally:
        if terminal["status"] == "IMPORT_NOT_DISPATCHED":
            intent = directory / "import-submit-intent.json"
            if intent.exists() or intent.is_symlink():
                terminal["status"] = "UNKNOWN"
        try:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                terminal["exit_code"] = process.wait(timeout=15)
        finally:
            publish(directory / "terminal.json", terminal)
    return terminal


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "Windows Kdenlive editor checkout differs from the workflow")
    print(json.dumps(run_windows(Path(os.environ["RUNNER_TEMP"]), context, distribution),
                     sort_keys=True))


if __name__ == "__main__":
    main()
