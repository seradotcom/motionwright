"""Observe the first native Kdenlive save and cold reopen of the hosted synthetic OTIO cut."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from bridge import KDENLIVE_REQUEST_SCHEMA, preview_kdenlive_cut
from kdenlive_native_distribution import JOB, ROOT, publish, selection
from kdenlive_native_installation import windows_accessibility_context
from kdenlive_native_source import MAX_METADATA_BYTES, admitted_source, inspect_initial_save
from kdenlive_readback import MAX_XML_BYTES, compare_two_native_custody_snapshots, inspect_saved_kdenlive
from source_checkout_observation import git, regular_file, require
from windows_updated_application import process_identity


def observe_window(powershell, editor, identity, path, digest, directory, *, verify_saved=False):
    arguments = [
        str(powershell), "-NoProfile", "-NonInteractive", "-File", str(Path(__file__).with_suffix(".ps1")),
        "-EditorPid", str(identity["pid"]), "-EditorCreationFiletime", str(identity["creation_filetime"]),
        "-Executable", str(editor), "-OriginalOtio", str(path), "-OriginalSha256", digest,
        "-Evidence", str(directory),
    ]
    if verify_saved:
        arguments.extend(("-VerifySavedProject", "-EditSyntheticReturn"))
    with (directory / "observer.stdout.log").open("xb") as stdout, (
            directory / "observer.stderr.log").open("xb") as stderr:
        result = subprocess.run(
            arguments, cwd=directory, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            timeout=80 if verify_saved else 235,
        )
    require(result.returncode == 0, f"Kdenlive window observer failed ({result.returncode})")


def verify_reopen(directory, context, distribution, powershell, environment, original, first, saved, first_readback):
    editor, _, source = original
    owner = directory.parent
    project = directory / "initial.kdenlive"
    output = owner / "windows-reopen"
    require(regular_file(project, MAX_XML_BYTES) == saved
            and admitted_source(owner, context, distribution) == original,
            "the saved project or original inputs changed before reopening")
    output.mkdir()
    profile = output / "profile"
    (profile / "roaming").mkdir(parents=True)
    (profile / "local").mkdir()
    reopen_environment = {**environment, "APPDATA": str(profile / "roaming"),
                          "LOCALAPPDATA": str(profile / "local")}
    publish(output / "reopen-intent.json", {
        "context": context, "original_project": saved, "source": source["manifest"],
        "first_process": first, "automatic_retry": False,
    })
    with (output / "application.stdout.log").open("xb") as stdout, (
            output / "application.stderr.log").open("xb") as stderr:
        process = subprocess.Popen(
            [str(editor), "--no-welcome", "--config", str(profile / "kdenliverc"), str(project)],
            cwd=editor.parent, env=reopen_environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        )
        try:
            reopened = process_identity(process, editor)
            require(reopened["creation_filetime"] > first["creation_filetime"],
                    "reopened project is not in a new Kdenlive process")
            observe_window(powershell, editor, reopened, project, saved["sha256"], output, verify_saved=True)
            window = json.loads((output / "reopen-window.json").read_bytes())
            require(window["editor_pid"] == reopened["pid"] and window["saved_sha256"] == saved["sha256"]
                    and window["submit_actions"] == 0 and process.poll() is None
                    and process_identity(process, editor) == reopened,
                    "the original Kdenlive project did not reopen in the new process")
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
    require(regular_file(project, MAX_XML_BYTES) == saved
            and admitted_source(owner, context, distribution) == original,
            "the saved project or original inputs changed after reopening")
    capsule = owner / "otio-original"
    received, returned = directory / "received.otio", output / "edited.otio"
    received_identity = regular_file(received, MAX_METADATA_BYTES)
    returned_identity = regular_file(returned, MAX_METADATA_BYTES)
    edited = output / "edited.kdenlive"
    edited_identity = regular_file(edited, MAX_XML_BYTES)
    manifest_sha = source["manifest"]["sha256"]
    edited_readback = inspect_saved_kdenlive(edited, edited_identity["sha256"], capsule, manifest_sha)
    comparison = compare_two_native_custody_snapshots(first_readback, edited_readback)
    target = first_readback["observed_bound_editorial_clip_semantics"][2]["scene_id"]
    require(comparison["bounded_native_end_trims_observed"] == [{
        "scene_id": target, "initial_duration_frames": 30, "reopened_duration_frames": 29,
    }], "saved Kdenlive return differs from the single planned end trim")
    preview = preview_kdenlive_cut(capsule, owner, {
        "schema": KDENLIVE_REQUEST_SCHEMA, "capsule_manifest_sha256": manifest_sha,
        "received_otio": received.relative_to(owner).as_posix(), "received_sha256": received_identity["sha256"],
        "edited_otio": returned.relative_to(owner).as_posix(), "edited_sha256": returned_identity["sha256"],
    }, capsule / "source-project.json", authorized_media_roots=(capsule,))
    require(preview["status"] == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED"
            and preview["edit_imported"] is False and preview["publication_approved"] is False
            and preview["native_change_proposals"] == [{
                "type": "set_scene_duration", "scene_id": target, "duration": {"num": "29", "den": "30"},
            }], "Motionwright preview did not preserve the native one-frame end trim")
    require(regular_file(edited, MAX_XML_BYTES) == edited_identity
            and regular_file(received, MAX_METADATA_BYTES) == received_identity
            and regular_file(returned, MAX_METADATA_BYTES) == returned_identity,
            "native Kdenlive return changed during readback")
    publish(output / "reopened-original.json", {
        "context": context, "original_project": saved, "first_process": first,
        "reopened_process": reopened, "verified_window": window, "source": source["manifest"],
        "edited_project": edited_identity, "initial_export": received_identity,
        "edited_export": returned_identity, "native_comparison": comparison, "review_preview": preview,
    })


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
            observe_window(powershell, editor, identity, source["timeline"]["path"],
                           source["timeline"]["sha256"], directory)
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
    verify_reopen(directory, context, distribution, powershell, environment,
                  (editor, binary, source), identity, observed, readback)
    return {**terminal, "status": "INITIAL_SAVE_REOPEN_AND_OTIO_RETURN_VERIFIED"}


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "Windows Kdenlive editor checkout differs from the workflow")
    print(json.dumps(run_windows(Path(os.environ["RUNNER_TEMP"]), context, distribution),
                     sort_keys=True))


if __name__ == "__main__":
    main()
