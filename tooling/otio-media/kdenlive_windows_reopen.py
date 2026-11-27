"""Reopen the retained Windows native save in a fresh installed Kdenlive process."""
from __future__ import annotations

from hashlib import sha256
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

from kdenlive_native_distribution import JOB, REPOSITORY, ROOT, publish, selection
from kdenlive_native_installation import windows_accessibility_context
from kdenlive_native_source import installed_editor
from kdenlive_readback import MAX_XML_BYTES, safe_mlt_xml
from source_checkout_observation import git, regular_file, require
from windows_updated_application import process_identity


SAVE_RUN = 46004747792
SAVE_SOURCE = "b794e65dd895dd758ff3a600c89a92ee184f085f"
SAVE_ARCHIVE = (15202300095, SAVE_RUN, "549cee109bdf4ddc9b2b625b2ed14050c1f5a8eb1a311c3cc478a1a42c1e16f3")
MEDIA_ARCHIVE = (15194566873, 45992655177, "600b556afb6e172990572e6db9e9630942265b3e027da4224f62d4537066ff0e")
SAVE_SHA = "b929bc33a141d9f5852ba8d79efd817b928823ab4d9b0c0f5ece7215aec7d550"
MEDIA_SHA = "140488b34cfb9ce0910f64fbcd14022b247026b8ac6f58610c5a9a6beae18e9e"
EDITOR_SHA = "c137523b19f70f11d01553c0bb6fb77df3677c3e583afd7ac59b77e2cfe09216"
SAVE_SIZE = 55986
MEDIA_SIZE = 274124
ARCHIVE_LIMIT = 2 * 1024 * 1024


def gh_read(resource):
    api = f"repos/{REPOSITORY}/actions/{resource}"
    return subprocess.check_output(["gh", "api", api], stdin=subprocess.DEVNULL, timeout=35)


def archive(original):
    ident, run, digest = original
    row = json.loads(gh_read(f"artifacts/{ident}"))
    require(row.get("id") == ident and row.get("workflow_run", {}).get("id") == run
            and row.get("expired") is False and row.get("digest") == "sha256:" + digest
            and 0 < row.get("size_in_bytes", 0) <= ARCHIVE_LIMIT,
            "retained Kdenlive archive identity differs")
    raw = gh_read(f"artifacts/{ident}/zip")
    require(len(raw) == row["size_in_bytes"] and sha256(raw).hexdigest() == digest,
            "retained Kdenlive archive bytes differ")
    return zipfile.ZipFile(io.BytesIO(raw))


def member(source, name, maximum):
    entries = [entry for entry in source.infolist() if entry.filename == name]
    require(len(entries) == 1 and 0 < entries[0].file_size <= maximum,
            "retained Kdenlive archive member is absent, repeated or oversized")
    return source.read(entries[0])


def restore(runner):
    run = json.loads(gh_read(f"runs/{SAVE_RUN}"))
    require(run.get("head_sha") == SAVE_SOURCE and run.get("run_attempt") == 1
            and run.get("status") == "completed" and run.get("conclusion") == "success",
            "the retained Windows first save did not complete successfully")
    with archive(SAVE_ARCHIVE) as original, archive(MEDIA_ARCHIVE) as earlier:
        saved = member(original, "windows-editor/initial.kdenlive", MAX_XML_BYTES)
        report = json.loads(member(original, "windows-editor/readback.json", 64 * 1024))
        media_name = f"media/{MEDIA_SHA}.mp4"
        media = member(earlier, f"otio-original/{media_name}", MEDIA_SIZE)
    require(len(saved) == SAVE_SIZE and sha256(saved).hexdigest() == SAVE_SHA
            and report["initial_saved_project"] == {"bytes": SAVE_SIZE, "sha256": SAVE_SHA}
            and report["context"]["source_sha"] == SAVE_SOURCE
            and report["context"]["run_id"] == str(SAVE_RUN)
            and len(media) == MEDIA_SIZE and sha256(media).hexdigest() == MEDIA_SHA,
            "native save or original H.264 bytes differ")
    observed = report["readback"]
    require(observed["observed_source_media"] == [{
        "authorized_bundle_relative": media_name, "original_media_bytes": MEDIA_SIZE,
        "original_media_sha256": MEDIA_SHA, "source_MLT_component_id": "chain0",
    }] and [(clip["original_inpoint_frame"], clip["duration_frames"])
            for clip in observed["observed_bound_editorial_clip_semantics"]] == [(0, 30), (30, 30), (60, 30)],
            "native save does not bind the original three source intervals")
    target = runner / f"{JOB}-{SAVE_RUN}-1"
    project_path = target / "windows-editor" / "initial.kdenlive"
    media_path = target / "otio-original" / media_name
    tree, _ = safe_mlt_xml(saved)
    references = {Path(prop.text) for prop in tree.iter("property")
                  if prop.attrib.get("name") == "resource" and (prop.text or "").casefold().endswith(".mp4")}
    require(references == {media_path}, "original native media location differs on this runner")
    target.mkdir()
    for path, data in ((project_path, saved), (media_path, media)):
        path.parent.mkdir(parents=True)
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    return project_path, media_path, report["process"]


def reopen(runner, context, distribution):
    require(context["runner_os"] == "Windows" and runner.is_absolute() and runner.resolve(strict=True) == runner
            and all(windows_accessibility_context().values()),
            "an interactive Windows editor job with a canonical runner root is required")
    owner = runner / f"{JOB}-{context['run_id']}-1"
    editor, binary = installed_editor(owner, context, distribution)
    require(binary["sha256"] == EDITOR_SHA, "installed editor is different from the first save")
    project, media, previous = restore(runner)
    project_identity = regular_file(project, MAX_XML_BYTES)
    media_identity = regular_file(media, MEDIA_SIZE)
    require(project_identity == {"bytes": SAVE_SIZE, "sha256": SAVE_SHA}
            and media_identity == {"bytes": MEDIA_SIZE, "sha256": MEDIA_SHA},
            "restored Kdenlive originals differ on disk")

    output = owner / "windows-reopen"
    output.mkdir()
    profile = output / "profile"
    profile.mkdir()
    (profile / "roaming").mkdir()
    (profile / "local").mkdir()
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    regular_file(powershell, 16 * 1024 * 1024)
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in {"GH_TOKEN", "GITHUB_TOKEN"}}
    environment.update({"APPDATA": str(profile / "roaming"), "LOCALAPPDATA": str(profile / "local"),
                        "LANG": "en_US.UTF-8", "LANGUAGE": "en_US", "LC_ALL": "en_US.UTF-8"})
    publish(output / "reopen-intent.json", {
        "context": context, "original_run": SAVE_RUN, "original_project": project_identity,
        "original_media": media_identity, "automatic_retry": False, "editor_argv_project": str(project),
    })
    with (output / "editor.stdout.log").open("xb") as stdout, (output / "editor.stderr.log").open("xb") as stderr:
        process = subprocess.Popen(
            [str(editor), "--no-welcome", "--config", str(profile / "kdenliverc"), str(project)],
            cwd=editor.parent, env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        )
        try:
            observed = process_identity(process, editor)
            require(observed["creation_filetime"] > previous["creation_filetime"],
                    "reopened project is not in a new Kdenlive process")
            subprocess.run(
                [str(powershell), "-NoProfile", "-NonInteractive", "-File",
                 str(Path(__file__).with_name("kdenlive_windows_editor.ps1")),
                 "-EditorPid", str(observed["pid"]), "-EditorCreationFiletime", str(observed["creation_filetime"]),
                 "-Executable", str(editor), "-OriginalOtio", str(project), "-OriginalSha256", SAVE_SHA,
                 "-Evidence", str(output), "-VerifySavedProject"],
                cwd=output, env=environment, stdin=subprocess.DEVNULL, check=True, timeout=80,
            )
            window = json.loads((output / "reopen-window.json").read_bytes())
            require(window["editor_pid"] == observed["pid"] and window["saved_sha256"] == SAVE_SHA
                    and process.poll() is None and process_identity(process, editor) == observed,
                    "reopened Kdenlive window does not bind the new process and original save")
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
    require(installed_editor(owner, context, distribution) == (editor, binary)
            and regular_file(project, MAX_XML_BYTES) == project_identity
            and regular_file(media, MEDIA_SIZE) == media_identity,
            "Kdenlive originals changed after reopening")
    report = {
        "schema": "motionwright.kdenlive-windows-original-reopened/1", "context": context,
        "saved_run": SAVE_RUN, "saved_source": SAVE_SOURCE, "save": project_identity, "media": media_identity,
        "first_process": previous, "reopened_process": observed, "verified_window": window,
        "import_confirmations": 0, "save_confirmations": 0, "automatic_retry": False,
    }
    publish(output / "reopened-original.json", report)
    return report


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "Windows Kdenlive reopen checkout differs from the workflow")
    print(json.dumps(reopen(Path(os.environ["RUNNER_TEMP"]), context, distribution), sort_keys=True))


if __name__ == "__main__":
    main()
