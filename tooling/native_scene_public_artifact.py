#!/usr/bin/env python3
"""Curate exact-source v0.1 Blender GLB / Manim MP4 without Broker receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from cross_app_public_artifact import PublicationError, read_bounded, require, strict_object

SCHEMA = "motionwright.v01-native-scene-public/1"
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SCENES = {
    "blender": {
        "media": "motionwright-contribution.glb",
        "evidence": "motionwright-blender-evidence.json",
        "asset_key": "glb",
        "required_count": 24,
        "commands": {
            "driver.blender.collection.create", "driver.blender.collection.link",
            "driver.blender.export.glb", "driver.blender.material.assign",
            "driver.blender.material.create", "driver.blender.object.create",
            "driver.blender.object.transform",
        },
        "max_bytes": 128_000_000,
    },
    "manim": {
        "media": "motionwright-manim-community.mp4",
        "evidence": "motionwright-manim-evidence.json",
        "asset_key": "mp4",
        "required_count": 10,
        "commands": {
            "driver.manim-community.doctor", "driver.manim-community.render.cancel",
            "driver.manim-community.render.result", "driver.manim-community.render.start",
            "driver.manim-community.render.status",
        },
        "max_bytes": 128_000_000,
    },
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verified_private_scene(root: Path, source: str, pin: str, scene: str) -> bytes:
    spec = SCENES[scene]
    require(root.is_dir() and not root.is_symlink(),
            "missing_or_linked_private_evidence_root")
    result = strict_object(read_bounded(root, "result.json", 128_000))
    receipt = strict_object(read_bounded(root, spec["evidence"], 1_000_000))
    asset = result.get(spec["asset_key"])
    original = receipt.get("result")
    project = result.get("project")
    require(
        result.get(f"native_{scene}_e2e") == "PASS"
        and receipt.get(f"native_{scene}_e2e") == "PASS"
        and result.get("motionwright_sha") == source
        and result.get("semwright_sha") == pin
        and result.get("semwright_version") == "1.0.0"
        and set(result.get("capabilities_checked", [])) == spec["commands"]
        and isinstance(project, dict)
        and all(isinstance(project.get(key), str) and project[key]
                for key in ("project_id", "generation", "scene_id"))
        and type(project.get("revision")) is int and project["revision"] >= 1
        and all(receipt.get(evidence_key) == project[project_key]
                for evidence_key, project_key in (
                    ("project_id", "project_id"), ("generation", "generation"),
                    ("revision", "revision"), ("scene_id", "scene_id")))
        and isinstance(original, dict)
        and original.get("native_driver") == ("driver:" + scene if scene == "blender"
                                               else "driver:manim-community")
        and isinstance(original.get("artifact"), dict)
        and type(receipt.get("receipt_count")) is int
        and receipt["receipt_count"] == spec["required_count"]
        and isinstance(receipt.get("completed_commands"), list)
        and len(receipt["completed_commands"]) >= 2,
        "native_scene_receipts_not_verified",
    )
    require(isinstance(asset, dict) and
            isinstance(asset.get("sha256"), str) and
            SHA256.fullmatch(asset["sha256"]) is not None and
            type(asset.get("bytes")) is int and 0 < asset["bytes"] <= spec["max_bytes"] and
            original["artifact"].get("sha256") == asset["sha256"] and
            original["artifact"].get("bytes") == asset["bytes"],
            "native_scene_artifact_identity_mismatch")
    blob = read_bounded(root, spec["media"], spec["max_bytes"])
    require(len(blob) == asset["bytes"] and digest(blob) == asset["sha256"],
            "native_scene_artifact_integrity_mismatch")
    if scene == "blender":
        require(len(blob) >= 12 and blob[:4] == b"glTF"
                and struct.unpack("<II", blob[4:12]) == (2, len(blob))
                and type(asset.get("meshes")) is int and asset["meshes"] >= 2
                and type(asset.get("materials")) is int and asset["materials"] >= 2,
                "blender_glb_structure_not_verified")
    else:
        require(len(blob) >= 12 and blob[4:8] == b"ftyp"
                and asset.get("width") == 640 and asset.get("height") == 360
                and asset.get("relative_path", "").endswith(".mp4"),
                "manim_mp4_structure_not_verified")
    return blob


def publish(root: Path, output: Path, source: str, pin: str,
            scene: str, job_status: str) -> dict:
    require(scene in SCENES, "unsupported_native_scene")
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(pin)),
            "native_scene_source_identity_invalid")
    require(job_status in {"success", "failure", "cancelled", "skipped"},
            "native_scene_ci_status_invalid")
    require(not output.exists(), "public_scene_artifact_already_exists")
    src = root.resolve()
    dst = output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_scene_overlaps_private_source")
    media = (verified_private_scene(root, source, pin, scene)
             if job_status == "success" else None)
    files = ([{
        "name": SCENES[scene]["media"],
        "sha256": digest(media),
        "bytes": len(media),
    }] if media is not None else [])
    receipt = {
        "schema": SCHEMA,
        "renderer": scene,
        "motionwright_source_sha": source,
        "semwright_source_pin": pin,
        "native_scene_ci_evidence": "PASS" if media is not None else "NOT_VERIFIED",
        "included_files": files,
        "raw_native_job_or_provider_receipts_uploaded": False,
        "owner_connection_or_session_material_uploaded": False,
        "raw_daemon_or_sandbox_logs_uploaded": False,
        "native_effects_replayed_by_publisher": False,
        "installed_owner_acceptance": "NOT_RUN",
        "v01_release_authorized": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    if media is not None:
        (output / SCENES[scene]["media"]).write_bytes(media)
    with (output / "public-evidence.json").open("x", encoding="utf-8") as f:
        json.dump(receipt, f, sort_keys=True, indent=2)
        f.write("\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args(argv)
    try:
        source_lock = strict_object(
            (Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json").read_bytes()
        )
        pin = source_lock["dependencies"]["semwright"]["revision"]
        receipt = publish(args.evidence_root, args.output_root,
                          args.source_sha, pin, args.scene, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError, ValueError) as exc:
        reason = exc.code if isinstance(exc, PublicationError) else "native_scene_publication_refused"
        print("native-scene-public-artifact=refused reason=" + reason, file=sys.stderr)
        return 2
    print("native-scene-public-artifact=" + receipt["native_scene_ci_evidence"] +
          " files=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
