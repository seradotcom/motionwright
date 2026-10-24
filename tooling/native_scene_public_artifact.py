#!/usr/bin/env python3
"""Curate exact-source v0.1 Blender GLB / Manim MP4 without Broker receipts."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
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
        # Manim async polling can add completed status observations.
        # The E2E caps receipt collection at 256, so require the original
        # operation stages instead of falsely demanding exactly 10 rows.
        "max_receipts": 256,
        "required_completed_commands": {
            "driver.manim-community.editable.render.start",
            "driver.manim-community.render.status",
            "driver.manim-community.render.result",
        },
        # The canonical seed/realize path uses saved editable source and its
        # font/clock contract. A legacy start is not evidence for this route.
        "allowed_completed_commands": {
            "driver.manim-community.doctor", "driver.manim-community.render.cancel",
            "driver.manim-community.render.result", "driver.manim-community.editable.render.start",
            "driver.manim-community.render.status",
        },
        "commands": {
            "driver.manim-community.doctor", "driver.manim-community.render.cancel",
            "driver.manim-community.render.result", "driver.manim-community.render.start",
            "driver.manim-community.editable.render.start",
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
    completed = receipt.get("completed_commands")
    count = receipt.get("receipt_count")
    # The native E2E's append-only journal records async polling attempts.
    # Exact rows vary with provider responsiveness; counting 10 precisely
    # rejected an otherwise verified real MP4. Do not relax any authority,
    # source, original stage or artifact digest check.
    manim_receipts_valid = (
        type(count) is int and 3 <= count <= spec["max_receipts"]
        and isinstance(completed, list) and len(completed) >= 3
        and all(isinstance(command, str) and command in spec["allowed_completed_commands"]
                for command in completed)
        and len(completed) == len(set(completed))
        and spec["required_completed_commands"] <= set(completed)
        and count >= len(completed)
    ) if scene == "manim" else True
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
        and manim_receipts_valid
        and (scene == "manim" or (
            type(count) is int and count == spec["required_count"]
            and isinstance(completed, list) and len(completed) >= 2
        )),
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


def verified_static_publication(root: Path, source_sha: str, semwright_sha: str,
                                sealed_originals: Path | None) -> tuple[bytes, dict]:
    """Read the one static outcome and its existing custody; never render or decrypt.

    The independent custodian still owns decryption. Header, digest and original
    file checks do not authenticate a producer or grant native authority.
    """
    require(sealed_originals is not None, "original_static_custody_required")
    # Keep every existing project, receipt-stage and media check before adding
    # custody admission. The new contract cannot hide a legacy rejection.
    media = verified_private_scene(root, source_sha, semwright_sha, "manim")
    from native_manim_camera_evidence import object_bytes, read
    from native_manim_e2e import checked_static_originals
    import ci_private_evidence as custody

    current = {"source_sha": source_sha, "semwright_sha": semwright_sha,
               "run_id": os.environ.get("GITHUB_RUN_ID"),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")}
    custody.validate_context(current)
    recipient = os.environ.get("PRIVATE_EVIDENCE_RECIPIENT_SHA256", "")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and current["run_attempt"] == "1"
            and type(recipient) is str and SHA256.fullmatch(recipient) is not None,
            "static_public_dispatch_and_recipient_required")
    paths = (
        "result.json", "static-render-verification.json", "motionwright-manim-evidence.json",
        "motionwright-manim-runtime-observation.json", "native-example.stdout.log",
        "native-invocation-intent.json", "original-terminal.json", "camera-custody.json",
        "runtime-inventory.json", "native-video-admission.json",
        "native-video-originals/decoded-video.json", "native-video-originals/source.py",
    )
    originals = {name: read(root / name) for name in paths}
    result = object_bytes(originals["result.json"])
    pending = object_bytes(originals["static-render-verification.json"])
    native = object_bytes(originals["native-example.stdout.log"])
    terminal = object_bytes(originals["original-terminal.json"])
    sealed = object_bytes(originals["camera-custody.json"])
    intent = object_bytes(originals["native-invocation-intent.json"])

    def same_json(left, right):
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == \
            json.dumps(right, sort_keys=True, separators=(",", ":"), allow_nan=False)

    require(result.get("native_manim_e2e") == "PASS"
            and result.get("motionwright_sha") == source_sha
            and result.get("semwright_sha") == semwright_sha
            and result.get("semwright_version") == "1.0.0"
            and result.get("manim_version") == "0.21.0"
            and type(result.get("native_effect_invocations")) is int
            and result["native_effect_invocations"] == 1
            and result.get("sealed_custody_required") is True
            and result.get("native_editable_reopen_verified") is False
            and result.get("human_acceptance") is False,
            "static_public_result_scope")
    expected_pending = {key: value for key, value in result.items()
                        if key not in ("sealed_originals_sha256", "sealed_originals_bytes")}
    expected_pending["native_manim_e2e"] = "PASS_REQUIRES_SEALED_CUSTODY"
    require(same_json(pending, expected_pending), "static_public_preseal_result_binding")
    _, digests = checked_static_originals(native, result["project"], root)
    require(all(result.get(key) == value for key, value in digests.items()),
            "static_public_original_digests")
    start = native.get("original_start_receipt", {})
    completed = native.get("original_result_receipt", {})
    request = native.get("request_id")
    require(type(request) is str and bool(request)
            and all(type(row) is dict and row.get("command") == command
                    and row.get("stage") == "completed"
                    and row.get("request_id") == request + suffix
                    and all(same_json(row.get(key), result["project"][key])
                            for key in ("project_id", "generation", "revision"))
                    for row, command, suffix in (
                        (start, "driver.manim-community.editable.render.start", ":start"),
                        (completed, "driver.manim-community.render.result", ":result")))
            and same_json(completed, native.get("result", {}).get("result", {}).get("receipt")),
            "static_public_original_editable_receipts")
    require(same_json(intent, {"context": current, "native_example_invocations": 1,
                               "automatic_retry": False}), "static_public_once_intent")
    process = terminal.get("original_process_observation", {})
    require(terminal.get("schema") == "motionwright.manim.camera.original-terminal/1"
            and terminal.get("native_example_invoked") is True
            and same_json(terminal.get("native_result"), native)
            and terminal.get("native_error") is None and terminal.get("later_error") is None
            and terminal.get("secondary_errors") == []
            and terminal.get("automatic_retry") is False
            and terminal.get("source_or_human_acceptance") is False
            and type(process) is dict and type(process.get("pid")) is int and process["pid"] > 0
            and type(process.get("process_returncode")) is int and process["process_returncode"] == 0
            and process.get("native_outcome") == "NOT_INTERPRETED"
            and process.get("automatic_retry") is False,
            "static_public_original_terminal")
    require(sealed.get("schema") == "motionwright.manim.camera.sealed-custody/1"
            and sealed.get("status") == "PASS"
            and sealed.get("selected_originals_complete") is True
            and sealed.get("native_result_retained") is True
            and same_json(sealed.get("context"), current)
            and sealed.get("recipient_sha256") == recipient
            and type(sealed.get("secondary_error_count")) is int and sealed["secondary_error_count"] == 0
            and sealed.get("original_owner_tree_removed") is False
            and sealed.get("atomic_sqlite_snapshot_claimed") is False
            and sealed.get("producer_authenticated_by_encryption") is False,
            "static_public_custody_incomplete")
    ciphertext = read(sealed_originals, 256 * 1024 * 1024)
    require(type(sealed.get("ciphertext_bytes")) is int
            and type(result.get("sealed_originals_bytes")) is int
            and len(ciphertext) == sealed["ciphertext_bytes"] == result["sealed_originals_bytes"]
            and digest(ciphertext) == sealed.get("ciphertext_sha256") == result.get("sealed_originals_sha256")
            and ciphertext.startswith(custody.MAGIC) and len(ciphertext) > len(custody.MAGIC) + 4,
            "static_public_ciphertext_missing_or_changed")
    offset = len(custody.MAGIC)
    length = int.from_bytes(ciphertext[offset:offset + 4], "big")
    require(0 < length <= 8192 and len(ciphertext) > offset + 4 + length,
            "static_public_seal_header_size")
    aad = ciphertext[offset + 4:offset + 4 + length]
    header = custody.strict(aad)
    require(set(header) == {"schema", "algorithm", "recipient_spki_sha256", "context",
                            "nonce", "wrapped_key", "plaintext_sha256", "plaintext_bytes"}
            and header["schema"] == custody.SEAL_SCHEMA
            and header["algorithm"] == "RSA-OAEP-SHA256+AES-256-GCM"
            and custody.json_bytes(header) == aad
            and same_json(header["context"], current)
            and header["recipient_spki_sha256"] == recipient
            and type(header["plaintext_bytes"]) is int
            and 0 < header["plaintext_bytes"] <= custody.MAX_TOTAL + custody.MAX_FILES * 4096
            and type(header["plaintext_sha256"]) is str
            and SHA256.fullmatch(header["plaintext_sha256"]) is not None
            and len(ciphertext) - offset - 4 - length == header["plaintext_bytes"] + 16,
            "static_public_seal_header_binding")
    require(len(base64.b64decode(header["nonce"], validate=True)) == 12
            and len(base64.b64decode(header["wrapped_key"], validate=True)) == 384,
            "static_public_seal_key_shape")
    inventory = object_bytes(originals["runtime-inventory.json"])
    video = object_bytes(originals["native-video-admission.json"])
    require(inventory.get("schema") == "motionwright.manim.runtime-inventory/2"
            and same_json(inventory.get("context"), current)
            and inventory.get("manim_version") == "0.21.0"
            and inventory.get("pyav_version") == "15.0.0"
            and video.get("schema") == "motionwright.manim.original-native-video-admission/1"
            and video.get("status") == "PASS"
            and video.get("authority") == "original_driver_rust_parser_and_receipt"
            and video.get("artifact_sha256") == result["mp4"]["sha256"]
            and video.get("report_sha256") == digest(originals["native-video-originals/decoded-video.json"])
            and video.get("source_sha256") == digest(originals["native-video-originals/source.py"])
            and type(video.get("native_decode_or_render_invocations")) is int
            and video["native_decode_or_render_invocations"] == 0,
            "static_public_native5_original_admission")
    require(all(read(root / name) == raw for name, raw in originals.items())
            and read(sealed_originals, 256 * 1024 * 1024) == ciphertext
            and read(root / SCENES["manim"]["media"], SCENES["manim"]["max_bytes"]) == media,
            "static_public_originals_changed_during_readback")
    return media, {
        "profile": "static-scene", "native_effect_invocations": 1,
        "native_source_route": "saved-editable-scene",
        "sealed_originals_sha256": sealed["ciphertext_sha256"],
        "sealed_originals_recipient_sha256": sealed["recipient_sha256"],
        "native_editable_reopen_verified": False, "human_acceptance": False,
        "producer_authenticated_by_encryption": False,
    }


def publish(root: Path, output: Path, source: str, pin: str,
            scene: str, job_status: str, profile: str = "static",
            sealed_originals: Path | None = None) -> dict:
    require(scene in SCENES, "unsupported_native_scene")
    require(profile in {"static", "camera120"}
            and (profile == "static" or scene == "manim"), "unsupported_native_scene_profile")
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(pin)),
            "native_scene_source_identity_invalid")
    require(job_status in {"success", "failure", "cancelled", "skipped"},
            "native_scene_ci_status_invalid")
    require(not output.exists(), "public_scene_artifact_already_exists")
    src = root.resolve()
    dst = output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_scene_overlaps_private_source")
    camera = None
    static = None
    if job_status == "success" and profile == "camera120":
        require(sealed_originals is not None, "original_camera_custody_required")
        from native_manim_camera_evidence import verified_publication
        media, camera = verified_publication(root, source, pin, sealed_originals)
    elif job_status == "success" and scene == "manim":
        media, static = verified_static_publication(root, source, pin, sealed_originals)
    else:
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
    if profile == "camera120":
        receipt["camera"] = camera or {
            "profile": "planar-camera-v1",
            "decoded_frame_count": None,
            "source_bound_two_axis_camera_motion": False,
            "native_editable_reopen_verified": False,
            "full_pixel_equality_verified": False,
            "human_acceptance": False,
        }
    elif scene == "manim":
        receipt["static"] = static or {
            "profile": "static-scene", "native_effect_invocations": None,
            "native_editable_reopen_verified": False, "human_acceptance": False,
            "producer_authenticated_by_encryption": False,
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
    parser.add_argument("--profile", choices=("static", "camera120"), default="static")
    parser.add_argument("--sealed-originals", type=Path)
    args = parser.parse_args(argv)
    try:
        source_lock = strict_object(
            (Path(__file__).resolve().parents[1] / "SOURCE_LOCK.json").read_bytes()
        )
        pin = source_lock["dependencies"]["semwright"]["revision"]
        receipt = publish(args.evidence_root, args.output_root,
                          args.source_sha, pin, args.scene, args.job_status,
                          args.profile, args.sealed_originals)
    except (PublicationError, OSError, KeyError, TypeError, ValueError) as exc:
        reason = exc.code if isinstance(exc, PublicationError) else "native_scene_publication_refused"
        print("native-scene-public-artifact=refused reason=" + reason, file=sys.stderr)
        return 2
    print("native-scene-public-artifact=" + receipt["native_scene_ci_evidence"] +
          " files=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
