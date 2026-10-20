#!/usr/bin/env python3
"""Create one first-party, fully bound original OTIO cut for real Kdenlive G2.

The existing OTIO ce22 physical source intentionally has 2 ExternalReference
clips and 1 MissingReference, so cannot qualify a FULL 3-scene application
round-trip. This new *separate* CI-only entrypoint builds one 90-frame H264,
uses the existing compiled Rust Motionwright exporter and owner-checked
portable media bridge, and binds ALL THREE original 30-frame source intervals.

It never imports OTIO in Kdenlive, launches an editor, saves/reopens .kdenlive,
performs native effects or grants a public artifact/release. A separately
authorized disposable Linux integration run must authenticate those actual
application operations. Preserve the original missing-reference fixture as
a separate negative, not a synthetic full receiver PASS.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from bridge import export_portable, file_probe

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_RUST_FIXTURE = ROOT / "target" / "debug" / "examples" / "otio_real_media_fixture"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_FRAME_RATE = 30
EXPECTED_SOURCE_FRAMES = 90
EXPECTED_SCENE_COUNT = 3
EXPECTED_SOURCE_INTERVALS = ((0, 30), (30, 30), (60, 30))
MAX_RUST_STDOUT = 2 * 1024 * 1024


class KdenliveCompleteFixtureRefused(ValueError):
    pass


def require(condition: object, reason: str) -> None:
    if not condition:
        raise KdenliveCompleteFixtureRefused(reason)


def sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def admitted_firstparty_bindings(
    project: dict, asset_id: str, media_sha: str, *, frames: int,
    fps: int,
) -> list[dict]:
    require(HEX64.fullmatch(media_sha) is not None
            and isinstance(project, dict)
            and isinstance(project.get("scenes"), list)
            and len(project["scenes"]) == EXPECTED_SCENE_COUNT
            and isinstance(asset_id, str)
            and frames == EXPECTED_SOURCE_FRAMES
            and fps == EXPECTED_FRAME_RATE,
            "original_Kdenlive_full_bound_fixture_does_not_match_90frame_source")
    source_assets = [
        asset for asset in project.get("assets", []) if isinstance(asset, dict)
        and asset.get("id") == asset_id
        and asset.get("media_type") == "video/mp4"
        and asset.get("content_sha256") == media_sha
    ]
    require(len(source_assets) == 1,
            "original_Kdenlive_owner_asset_does_not_match_firstparty_source_SHA")
    ids = [scene.get("id") for scene in project["scenes"]]
    require(len(set(ids)) == EXPECTED_SCENE_COUNT
            and all(isinstance(i, str) and len(i) >= 8 for i in ids),
            "original_Kdenlive_three_authored_scene_IDs_not_unique")
    bindings = []
    for scene, (start, count) in zip(project["scenes"], EXPECTED_SOURCE_INTERVALS):
        require(isinstance(scene, dict) and start >= 0
                and start + count <= frames
                and scene.get("duration") == {"num": "1", "den": "1"},
                "firstparty_OTIO_scene_duration_or_source_interval_changed")
        bindings.append({
            "scene_id": scene["id"], "asset_id": asset_id,
            "file": "original.mp4", "sha256": media_sha,
            "source_start_frame": start,
            "rights": {
                "owner": "Original Motionwright selected G2 synthetic fixture",
                "license": "First-party synthetic H264 generated in disposable CI",
                "authorized": True, "redistributable": True,
            },
        })
    return bindings


def require_full_original_manifest(manifest: dict, project: dict,
                                   media_sha: str) -> dict:
    scenes = [s["id"] for s in project["scenes"]]
    rows = manifest.get("bound_media", [])
    require(manifest.get("schema") == "motionwright.otio-portable-media-bridge/1"
            and manifest.get("output_profile") == "portable-media-cut-v2"
            and manifest.get("scene_order") == scenes
            and manifest.get("missing_scene_ids") == []
            and isinstance(rows, list)
            and len(rows) == EXPECTED_SCENE_COUNT
            and manifest.get("nle_import_verified") is False
            and manifest.get("external_program_executed") is False
            and manifest.get("publishing_authorized") is False,
            "original_full_OTIO_owner_capsule_not_three_bound_unapproved_scenes")
    observed = [
        (row.get("scene_id"), row.get("source_sha256"),
         row.get("source_fps"), row.get("source_start_frame"),
         row.get("duration_frames"))
        for row in rows
    ]
    expected = [
        (scene_id, media_sha, EXPECTED_FRAME_RATE, start, count)
        for scene_id, (start, count) in zip(scenes, EXPECTED_SOURCE_INTERVALS)
    ]
    require(set(observed) == set(expected) and len(set(observed)) == 3,
            "full_Kdenlive_owner_scene_media_inpoint_mapping_not_exact")
    return {
        "schema": "motionwright.native.kdenlive-complete-firstparty-capsule/1",
        "source_media_sha256": media_sha,
        "source_decoded_frames": EXPECTED_SOURCE_FRAMES,
        "source_fps": EXPECTED_FRAME_RATE,
        "fully_bound_scene_count": 3,
        "missing_reference_count": 0,
        "original_scene_inpoints": [start for start, _ in EXPECTED_SOURCE_INTERVALS],
        "actual_Kdenlive_native_editor_import_or_reopen": False,
        "human_or_rights_independent_acceptance": False,
        "release_authorized": False,
    }


def require_ephemeral_approved_source(destination: Path) -> Path:
    require(sys.platform == "linux"
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("CI") == "true"
            and os.environ.get("MOTIONWRIGHT_APPROVED_KDENLIVE_G2_SOURCE") == "1",
            "real_Kdenlive_G2_fixture_must_be_selected_in_disposable_Linux_CI")
    temp = Path(os.environ.get("RUNNER_TEMP", ""))
    require(temp.is_absolute() and temp.is_dir() and not temp.is_symlink()
            and destination.is_absolute() and not destination.exists()
            and destination.parent == temp
            and not destination.is_symlink(),
            "original_Kdenlive_G2_fixture_destination_not_fresh_runner_owned")
    require(ORIGINAL_RUST_FIXTURE.is_file(),
            "original_current_Motionwright_Rust_OTIO_exporter_was_not_built")
    head = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", head) is not None,
            "Kdenlive_G2_source_head_is_not_pinned")
    actual = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=20, check=True,
    ).stdout.strip()
    require(actual == head,
            "Kdenlive_G2_Rust_exporter_and_bridge_are_not_at_CI_head")
    return temp


def _new_original(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _retain_call(prefix: Path, *, returncode: int | None,
                 stdout: bytes | None, stderr: bytes | None,
                 error: BaseException | None = None) -> None:
    # Record the original terminal before output admission or downstream parsing.
    streams = {"stdout": stdout, "stderr": stderr}
    require(all(value is None or isinstance(value, bytes) for value in streams.values()),
            "original_Kdenlive_G2_capture_is_not_binary")
    record = {
        "schema": "motionwright.native.firstparty-source-call/1",
        "state": "TERMINAL_OBSERVED" if error is None else "ERROR_OR_UNKNOWN",
        "returncode": returncode, "invocation_retried": False,
        "timeout_policy": "existing_subprocess_run",
        "error": None if error is None else {
            "type": type(error).__name__, "message": str(error)},
        "captured_streams": {
            name: {"captured": value is not None,
                   "bytes": None if value is None else len(value),
                   "sha256": None if value is None else hashlib.sha256(value).hexdigest()}
            for name, value in streams.items()},
    }
    _new_original(prefix.with_name(prefix.name + ".terminal.json"),
                  (json.dumps(record, sort_keys=True, indent=2) + "\n").encode())
    for name, value in streams.items():
        if value is not None:
            _new_original(prefix.with_name(prefix.name + "." + name + ".bin"), value)


def run_firstparty_native(cmd: list[str], *, timeout: int = 120,
                          evidence_prefix: Path | None = None) -> bytes:
    require(len(cmd) >= 1 and all(isinstance(arg, str) for arg in cmd),
            "original_Kdenlive_G2_source_command_invalid")
    if evidence_prefix is not None:
        require(evidence_prefix.is_absolute()
                and evidence_prefix.parent.resolve(strict=True) == evidence_prefix.parent
                and evidence_prefix.parent.is_dir()
                and all(not evidence_prefix.with_name(evidence_prefix.name + suffix).exists()
                        and not evidence_prefix.with_name(evidence_prefix.name + suffix).is_symlink()
                        for suffix in (".intent.json", ".terminal.json",
                                       ".stdout.bin", ".stderr.bin")),
                "original_Kdenlive_G2_call_evidence_not_fresh")
        _new_original(evidence_prefix.with_name(evidence_prefix.name + ".intent.json"),
                      (json.dumps({"state": "INTENT", "command": cmd, "timeout": timeout,
                                   "invocation_retried": False}, sort_keys=True) + "\n").encode())
    try:
        # Preserve the existing producer and its subprocess.run timeout policy.
        result = subprocess.run(
            cmd, capture_output=True, check=False, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except BaseException as error:
        if evidence_prefix is not None:
            try:
                _retain_call(evidence_prefix, returncode=None,
                             stdout=getattr(error, "stdout", None),
                             stderr=getattr(error, "stderr", None), error=error)
            except BaseException as retention_error:
                error.add_note("Original source-call evidence retention failed: "
                               + type(retention_error).__name__ + ": " + str(retention_error))
        raise
    if evidence_prefix is not None:
        _retain_call(evidence_prefix, returncode=result.returncode,
                     stdout=result.stdout, stderr=result.stderr)
    require(result.returncode == 0
            and len(result.stdout) <= MAX_RUST_STDOUT
            and len(result.stderr) <= 32 * 1024,
            "original_Kdenlive_G2_source_generator_or_video_failed")
    return result.stdout


@contextmanager
def firstparty_workspace(temp: Path, evidence_root: Path | None = None):
    if evidence_root is None:
        with tempfile.TemporaryDirectory(prefix="mw-firstparty-otio3-", dir=temp) as scratch:
            yield Path(scratch)
        return
    require(temp.is_absolute() and temp.resolve(strict=True) == temp
            and evidence_root.is_absolute() and evidence_root != temp
            and evidence_root.is_relative_to(temp)
            and evidence_root.parent.resolve(strict=True) == evidence_root.parent
            and not evidence_root.exists() and not evidence_root.is_symlink(),
            "original_Kdenlive_G2_retained_workspace_not_fresh_runner_owned")
    evidence_root.mkdir(mode=0o700)
    # The selected caller seals this original tree on success, failure or UNKNOWN.
    # Do not clean it up while unwinding a failed producer or bridge.
    yield evidence_root


def prepare_original(destination: Path, *, evidence_root: Path | None = None) -> dict:
    temp = require_ephemeral_approved_source(destination)
    # All side effects remain one new disposable GH runner and do not touch
    # Motionwright project source, canonical Studio, the Host or any NLE.
    with firstparty_workspace(temp, evidence_root) as root:
        original = root / "original.mp4"
        require(not original.exists(),
                "original_Kdenlive_G2_firstparty_media_not_fresh")
        run_firstparty_native([
            "ffmpeg", "-nostdin", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=3",
            "-frames:v", "90", "-an", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(original),
        ], evidence_prefix=None if evidence_root is None else root / "ffmpeg")
        measured = file_probe(original)
        require(measured["fps"] == EXPECTED_FRAME_RATE
                and measured["frames"] == EXPECTED_SOURCE_FRAMES
                and measured["codec"] == "h264",
                "synthetic_90frame_H264_not_physically_decoded_for_original")
        media_sha = sha(original)
        generated = json.loads(
            run_firstparty_native([str(ORIGINAL_RUST_FIXTURE), media_sha], timeout=90,
                                  evidence_prefix=None if evidence_root is None else root / "rust-exporter")
        )
        require(generated.get("schema") == "motionwright.real-otio-fixture/1"
                and isinstance(generated.get("project"), dict)
                and isinstance(generated.get("original_otio"), dict),
                "original_Rust_OTIO_exporter_return_not_source_bound")
        project = generated["project"]
        bindings = admitted_firstparty_bindings(
            project, generated["owner_media_asset_id"], media_sha,
            frames=measured["frames"], fps=measured["fps"],
        )
        for name, content in (
            ("project.json", project), ("original.otio", generated["original_otio"]),
        ):
            with (root / name).open("x", encoding="utf-8") as output:
                json.dump(content, output, sort_keys=True, indent=2)
                output.write("\n")
        request = {
            "schema": "motionwright.otio-portable-media-bridge/1",
            "project": "project.json", "otio": "original.otio",
            "bindings": bindings,
            "owner_review": {
                "reviewer": "Original first-party synthetic G2 test owner",
                "synthetic_source_allowed": True,
            },
        }
        result = export_portable(root, request, destination)
        require(result["verified_media_refs"] == 3
                and result["missing_refs"] == 0
                and result["owner_publication_authorized"] is False,
                "original_Kdenlive_G2_export_invented_or_lost_source_binding")
        manifest = json.loads(
            (destination / "bridge-manifest.json").read_bytes()
        )
        inspection = require_full_original_manifest(manifest, project, media_sha)
        inspection["GitHub_original_source_sha"] = os.environ["GITHUB_SHA"]
        inspection["compiled_original_Rust_OTIO_exporter_sha256"] = sha(
            ORIGINAL_RUST_FIXTURE
        )
        inspection["original_portable_manifest_sha256"] = sha(
            destination / "bridge-manifest.json"
        )
        original_target = destination / "media" / f"{media_sha}.mp4"
        require(original_target.is_file()
                and sha(original_target) == media_sha
                and measured == file_probe(original_target),
                "portable_firstparty_H264_changed_after_copy")
        # Private first-party source status; never mark external editor PASS.
        result_path = destination / "firstparty-complete-native-G2-input.json"
        with result_path.open("x", encoding="utf-8") as stream:
            json.dump(inspection, stream, sort_keys=True, indent=2)
            stream.write("\n")
        return inspection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = prepare_original(args.destination)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
