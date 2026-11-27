"""Cold reopen the verified macOS project and original media in its own job."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from kdenlive_native_distribution import JOB, ROOT, publish, selection
from kdenlive_macos_editor import Accessibility, EditorUI, editor_pid_executable
from kdenlive_native_source import MAX_METADATA_BYTES, installed_editor
from kdenlive_readback import MAX_MEDIA, MAX_XML_BYTES
from source_checkout_observation import git, regular_file, require


@dataclass(frozen=True)
class Original:
    executable: Path
    binary: dict
    first_pid: int
    project: Path
    saved: dict
    media: Path
    media_identity: dict

    def verify_files(self):
        require(regular_file(self.project, MAX_XML_BYTES) == self.saved
                and regular_file(self.media, MAX_MEDIA) == self.media_identity,
                "original saved project or H.264 changed")


def read_receipt(path):
    identity = regular_file(path, MAX_METADATA_BYTES)
    with path.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    require(len(raw) == identity["bytes"] and sha256(raw).hexdigest() == identity["sha256"],
            "original Kdenlive receipt changed during read")
    value = json.loads(raw)
    require(type(value) is dict, "invalid original Kdenlive receipt")
    return value


def admit_original(owner, context, distribution):
    executable, binary = installed_editor(owner, context, distribution)
    evidence = owner / "macos-initial"
    project = evidence / "initial.kdenlive"
    report = read_receipt(evidence / "initial-project.json")
    terminal = read_receipt(evidence / "editor-terminal.json")
    saved = read_receipt(evidence / "project-saved.json")
    source = read_receipt(owner / "otio-source-ready.json")
    readback = read_receipt(evidence / "initial-readback.json")
    first_pid = report.get("pid")
    identity = {"bytes": saved.get("bytes"), "sha256": saved.get("sha256")}
    require(report.get("schema") == "motionwright.kdenlive-macos-initial-project/1"
            and report.get("status") == "INITIAL_PROJECT_VERIFIED"
            and report.get("context") == source.get("context") == context
            and report.get("editor") == {"path": str(executable), **binary}
            and report.get("source") == source.get("timeline")
            and report.get("project") == {"path": str(project), **identity}
            and saved == {"pid": first_pid, "path": str(project), **identity}
            and report.get("import_confirmations") == report.get("save_confirmations") == 1
            and report.get("render_dispatches") == 0
            and type(first_pid) is int and first_pid > 0
            and terminal.get("pid") == first_pid and type(terminal.get("exit_code")) is int
            and readback.get("native_file_sha256") == identity["sha256"]
            and readback.get("exact_original_source_SHA_set_conserved") is True,
            "original Kdenlive save is not the verified closed process in this job")
    sources = readback.get("observed_source_media", [])
    unique = {(row.get("authorized_bundle_relative"), row.get("original_media_sha256"),
               row.get("original_media_bytes")) for row in sources}
    require(len(unique) == 1, "original Kdenlive save does not identify the single synthetic H.264")
    reference, media_sha, media_bytes = unique.pop()
    require(isinstance(media_sha, str) and re.fullmatch(r"[0-9a-f]{64}", media_sha)
            and type(media_bytes) is int and 0 < media_bytes <= MAX_MEDIA
            and reference == f"media/{media_sha}.mp4",
            "original H.264 path does not match its verified identity")
    original = Original(executable, binary, first_pid, project, identity,
                        owner / "otio-original" / reference,
                        {"bytes": media_bytes, "sha256": media_sha})
    original.verify_files()
    return original


def run(owner, context, distribution):
    original = admit_original(owner, context, distribution)
    output = owner / "macos-reopen"
    output.mkdir(mode=0o700)
    command = [str(original.executable), "--no-welcome", "--config", str(output / "kdenliverc"),
               "-platformtheme", "generic", str(original.project)]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DYLD_", "QT_", "QML", "MLT_"))}
    environment.update({"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "QT_ACCESSIBILITY": "1"})
    ax = Accessibility()
    try:
        publish(output / "reopen-intent.json", {
            "context": context, "argv": command, "first_pid": original.first_pid,
            "save": original.saved, "media": original.media_identity,
        })
        original.verify_files()
        with (output / "editor.stdout").open("xb") as stdout, (output / "editor.stderr").open("xb") as stderr:
            try:
                process = subprocess.Popen(command, cwd=output, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr)
            except OSError as error:
                publish(output / "editor-terminal.json", {"pid": None, "launch_error": str(error)})
                raise
            try:
                def guard():
                    require(process.poll() is None and process.pid != original.first_pid
                            and editor_pid_executable(process.pid) == original.executable,
                            "reopen does not own a distinct live Kdenlive process")

                guard()
                ax.attach(process.pid)
                ui = EditorUI(ax, guard)
                expected = "initial / 320x180 30FPS"

                def exact_window():
                    window = ui.top_window()
                    if not ax.owns(window) or ax.text(window, "AXTitle") != expected:
                        return None
                    if any(label in ui.title(panel) for panel in ui.panels()
                           for label in ("clip problems", "missing clips", "loading project")):
                        return None
                    return window

                window = ui.await_target(exact_window, "exact initial Kdenlive project in the new PID",
                                         timeout=60)
                observation = {"pid": process.pid, "window_title": ax.text(window, "AXTitle"),
                               "project_sha256": original.saved["sha256"]}
                publish(output / "reopen-window.json", observation)
                original.verify_files()
            except Exception as error:
                publish(output / "reopen-error.json", {"type": type(error).__name__, "message": str(error)})
                raise
            finally:
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                finally:
                    publish(output / "editor-terminal.json", {"pid": process.pid, "exit_code": process.poll()})
        original.verify_files()
        require(installed_editor(owner, context, distribution) == (original.executable, original.binary),
                "original Kdenlive executable changed during the reopen")
        report = {"schema": "motionwright.kdenlive-macos-original-reopened/1", "context": context,
                  "first_pid": original.first_pid, "reopened_process": observation,
                  "save": original.saved, "media": original.media_identity,
                  "import_confirmations": 0, "save_confirmations": 0, "render_dispatches": 0}
        publish(output / "reopened-original.json", report)
        return report
    finally:
        ax.close()


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(context["runner_os"] == "macOS", "macOS original reopen is restricted to macOS")
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "macOS Kdenlive reopen checkout differs from the workflow")
    runner = Path(os.environ["RUNNER_TEMP"])
    require(runner.is_absolute() and runner.resolve(strict=True) == runner,
            "macOS Kdenlive reopen runner root is not canonical")
    print(json.dumps(run(runner / f"{JOB}-{context['run_id']}-1", context, distribution),
                     sort_keys=True))


if __name__ == "__main__":
    main()
