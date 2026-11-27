"""Prepare the existing synthetic OTIO cut in its installed-editor job."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from bridge import KDENLIVE_REQUEST_SCHEMA, preview_kdenlive_cut
from kdenlive_native_distribution import JOB, ROOT, publish, selection
from kdenlive_complete_g2_fixture import EXPECTED_SOURCE_INTERVALS, produce_original
from kdenlive_native_installation import MAX_EDITOR_BYTES, windows_user_installation_root
from kdenlive_readback import (MAX_XML_BYTES, compare_two_native_custody_snapshots,
                               inspect_saved_kdenlive, read_capsule)
from source_checkout_observation import git, regular_file, require


MAX_METADATA_BYTES = 2 * 1024 * 1024


def installed_editor(owner: Path, context: dict, distribution) -> tuple[Path, dict]:
    record = owner / "installation.json"
    receipt = regular_file(record, MAX_METADATA_BYTES)
    record_raw = record.read_bytes()
    observed = json.loads(record_raw)
    require(sha256(record_raw).hexdigest() == receipt["sha256"]
            and observed.get("schema") == "motionwright.kdenlive-native-installation/1"
            and observed.get("context") == context
            and observed.get("archive", {}).get("sha256") == distribution.sha256,
            "installed Kdenlive receipt is not from this hosted job")
    relative = observed.get("relative_executable", "")
    if context["runner_os"] == "Windows":
        installation_root = windows_user_installation_root()
        require(relative == "bin/kdenlive.exe", "installed Kdenlive Windows path differs")
        binary_format = "PE64_x86_64"
    else:
        installation_root = owner
        parts = relative.split("/")
        require(len(parts) == 5 and parts[0] == "installed"
                and parts[1].casefold() == "kdenlive.app"
                and parts[2:] == ["Contents", "MacOS", "kdenlive"],
                "installed Kdenlive macOS bundle path differs")
        binary_format = "MACHO64_arm64"
    require(observed.get("installation_root") == str(installation_root),
            "installed Kdenlive root differs")
    executable = installation_root / relative
    binary = regular_file(executable, MAX_EDITOR_BYTES)
    require(binary["bytes"] > 0 and observed.get("original_executable") == {
        **binary, "format": binary_format,
    }, "installed Kdenlive executable differs from the original receipt")
    return executable, binary


def admitted_source(owner, context, distribution):
    receipt = owner / "otio-source-ready.json"
    receipt_identity = regular_file(receipt, MAX_METADATA_BYTES)
    with receipt.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    require(sha256(raw).hexdigest() == receipt_identity["sha256"],
            "source-ready receipt changed during read")
    source = json.loads(raw)
    editor, binary = installed_editor(owner, context, distribution)
    require(source.get("schema") == "motionwright.kdenlive-native-source/1"
            and source.get("context") == context
            and source.get("editor_path") == str(editor)
            and source.get("editor_sha256") == binary["sha256"],
            "editor source-ready receipt differs from this installation")
    capsule = owner / "otio-original"
    for role, filename in (("timeline", "timeline.otio"), ("manifest", "bridge-manifest.json")):
        path = capsule / filename
        require(source.get(role) == {"path": str(path), **regular_file(path, MAX_METADATA_BYTES)},
                f"editor source {role} differs from its original")
    read_capsule(capsule, source["manifest"]["sha256"])
    return editor, binary, source


def inspect_initial_save(saved: Path, saved_sha: str, manifest_sha: str, capsule: Path) -> dict:
    original = inspect_saved_kdenlive(saved, saved_sha, capsule, manifest_sha)
    clips = original["observed_bound_editorial_clip_semantics"]
    require(tuple((clip["original_inpoint_frame"], clip["duration_frames"]) for clip in clips)
            == EXPECTED_SOURCE_INTERVALS,
            "saved Kdenlive project does not preserve the three complete input intervals")
    return original


def inspect_native_return(owner: Path, manifest_sha: str, first_readback: dict,
                          received: Path, returned: Path, edited: Path) -> dict:
    capsule = owner / "otio-original"
    received_identity = regular_file(received, MAX_METADATA_BYTES)
    returned_identity = regular_file(returned, MAX_METADATA_BYTES)
    edited_identity = regular_file(edited, MAX_XML_BYTES)
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
    return {
        "edited_project": edited_identity, "initial_export": received_identity,
        "edited_export": returned_identity, "native_comparison": comparison, "review_preview": preview,
    }


def fixture_executable(context: dict) -> Path:
    name = "otio_real_media_fixture"
    if context["runner_os"] == "Windows":
        name += ".exe"
    return ROOT / "target" / "debug" / "examples" / name


def video_tools() -> dict:
    observed = {}
    for name in ("ffmpeg", "ffprobe"):
        match = shutil.which(name)
        require(match is not None and Path(match).is_absolute(),
                f"{name} is missing from the hosted job")
        executable = Path(match).resolve(strict=True)
        measured = regular_file(executable, 256 * 1024 * 1024)
        require(measured["bytes"] > 0, f"{name} is empty")
        observed[name] = {"path": str(executable), **measured}
    return observed


def prepare_native_source(runner: Path, context: dict, distribution) -> dict:
    require(runner.is_absolute() and runner.resolve(strict=True) == runner,
            "native editor source runner root is not canonical")
    owner = runner / f"{JOB}-{context['run_id']}-1"
    editor, editor_original = installed_editor(owner, context, distribution)
    exporter = fixture_executable(context)
    original_exporter = regular_file(exporter, MAX_EDITOR_BYTES)
    tools = video_tools()
    destination = owner / "otio-original"
    evidence = owner / "otio-source-evidence"
    publish(owner / "otio-source-intent.json", {
        "context": context, "editor_sha256": editor_original["sha256"],
        "exporter": original_exporter, "tools": tools,
    })
    require(not destination.exists() and not destination.is_symlink(),
            "native editor OTIO source directory is already present")
    original = produce_original(destination, temp=runner, exporter=exporter, audio_source=False,
                                evidence_root=evidence)
    require(installed_editor(owner, context, distribution) == (editor, editor_original)
            and regular_file(exporter, MAX_EDITOR_BYTES) == original_exporter
            and original.get("GitHub_original_source_sha") == context["source_sha"]
            and original.get("compiled_original_Rust_OTIO_exporter_sha256")
            == original_exporter["sha256"]
            and video_tools() == tools,
            "native editor source tooling or installed Kdenlive changed during preparation")
    timeline = destination / "timeline.otio"
    manifest = destination / "bridge-manifest.json"
    timeline_identity = regular_file(timeline, MAX_METADATA_BYTES)
    manifest_identity = regular_file(manifest, MAX_METADATA_BYTES)
    manifest_raw = manifest.read_bytes()
    require(sha256(manifest_raw).hexdigest() == manifest_identity["sha256"]
            == original.get("original_portable_manifest_sha256")
            and json.loads(manifest_raw).get("otio_sha256") == timeline_identity["sha256"],
            "native OTIO manifest does not bind the prepared timeline")
    report = {
        "schema": "motionwright.kdenlive-native-source/1", "context": context,
        "editor_path": str(editor), "editor_sha256": editor_original["sha256"],
        "timeline": {"path": str(timeline), **timeline_identity},
        "manifest": {"path": str(manifest), **manifest_identity},
    }
    publish(owner / "otio-source-ready.json", report)
    return report


def main():
    context, distribution = selection(os.environ, sys.platform, platform.machine())
    require(git(ROOT, "rev-parse", "HEAD").decode("ascii").strip() == context["source_sha"],
            "native Kdenlive source checkout differs from the workflow")
    print(json.dumps(prepare_native_source(Path(os.environ["RUNNER_TEMP"]), context, distribution),
                     sort_keys=True))


if __name__ == "__main__":
    main()
