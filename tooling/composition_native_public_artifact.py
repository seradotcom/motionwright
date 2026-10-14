#!/usr/bin/env python3
"""Public v0.5 joint-master evidence: only verified synthetic MP4 and contact sheet.

An owner-private native evidence directory never becomes an Actions artifact.
Publishing does not restart native work or authorize aesthetic/rights acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from cross_app_public_artifact import PublicationError, SHA, read_bounded, require, strict_object

SCHEMA = "motionwright.v05-joint-master-public-evidence/1"
MEDIA = {"composition-native-contact-sheet.png": 24_000_000,
         "motionwright-master.mp4": 180_000_000}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish(root: Path, destination: Path, mw_sha: str, sw_sha: str, status: str) -> dict:
    require(bool(SHA.fullmatch(mw_sha)) and bool(SHA.fullmatch(sw_sha)),
            "source_identity_invalid")
    require(status in {"success", "failure", "cancelled", "skipped"}, "ci_status_invalid")
    require(not destination.exists() and not destination.is_symlink(),
            "public_artifact_already_exists")
    src = root.resolve()
    dst = destination.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "public_and_private_roots_overlap")
    evidence = []
    copied = {}
    if status == "success":
        folder = root / mw_sha / "composition-stack"
        require(folder.is_dir() and not folder.is_symlink(), "private_evidence_missing")
        result = strict_object(read_bounded(folder, "result.json", 180_000))
        project_bytes = read_bounded(folder, "editable-project.json", 3_000_000)
        project = strict_object(project_bytes)
        review_bytes = read_bounded(folder, "composition-native-inspection.json", 1_000_000)
        review = strict_object(review_bytes)
        seed = result.get("project", {})
        require(isinstance(seed, dict) and
                result.get("native_av_master_e2e") == "PASS"
                and result.get("motionwright_sha") == mw_sha
                and result.get("semwright_sha") == sw_sha
                and result.get("fixture") == "composition-stack"
                and result.get("frame_count") == 540
                and result.get("decoded_video_frames") == 540
                and seed.get("fixture") == "composition-stack/1"
                and seed.get("original_ui_not_real_capture") is True
                and seed.get("data_truth") == "synthetic_fixture_not_real_world_metric"
                and isinstance(project.get("scenes"), list)
                and len(project["scenes"]) == 3
                and review.get("technical_sampling") == "PASS"
                and review.get("source_truth") == "first-party-original-and-synthetic-only"
                and review.get("frame_indices") == [140, 270, 480]
                and review.get("source_project_sha256") ==
                    digest(project_bytes)
                and review.get("decoded_master", {}).get("technical_sampling") == "PASS"
                and review.get("decoded_master", {}).get("frame_indices") == [140, 270, 480]
                and result.get("joint_native_pixel_evidence_sha256") == digest(review_bytes),
                "unverified_joint_native_source")
        for name, limit in MEDIA.items():
            copied[name] = read_bounded(folder, name, limit)
        require(copied["composition-native-contact-sheet.png"].startswith(b"\x89PNG\r\n\x1a\n")
                and copied["motionwright-master.mp4"][4:8] == b"ftyp",
                "invalid_verified_media_format")
        require(review.get("contact_sheet", {}).get("path") == "composition-native-contact-sheet.png"
                and review["contact_sheet"]["sha256"] == digest(copied["composition-native-contact-sheet.png"])
                and review["decoded_master"]["mp4_sha256"] == digest(copied["motionwright-master.mp4"])
                and result.get("master_sha256") == digest(copied["motionwright-master.mp4"]),
                "joint_media_digest_mismatch")
        evidence = [{"name": name, "bytes": len(blob), "sha256": digest(blob)}
                    for name, blob in sorted(copied.items())]

    receipt = {"schema": SCHEMA, "motionwright_source_sha": mw_sha,
               "semwright_source_pin": sw_sha,
               "ci_evidence": "PASS" if status == "success" else "NOT_VERIFIED",
               "included_files": evidence,
               "raw_native_receipts_uploaded": False,
               "raw_daemon_logs_uploaded": False,
               "source_project_json_uploaded": False,
               "native_mutation_replayed_by_publisher": False,
               "source_rights_accepted": False, "human_creative_review": "NOT_RUN",
               "v05_release_ready": False}
    destination.mkdir(parents=True, exist_ok=False)
    for name, blob in sorted(copied.items()):
        with (destination / name).open("xb") as out:
            out.write(blob)
    with (destination / "public-evidence.json").open("x", encoding="utf-8") as out:
        json.dump(receipt, out, sort_keys=True, indent=2)
        out.write("\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        lock = strict_object((Path(__file__).resolve().parents[1] /
                              "SOURCE_LOCK.json").read_bytes())
        sw_sha = lock["dependencies"]["semwright"]["revision"]
        result = publish(args.evidence_root, args.output_root,
                         args.source_sha, sw_sha, args.job_status)
    except (PublicationError, OSError, KeyError, TypeError, ValueError) as error:
        reason = error.code if isinstance(error, PublicationError) else "public_artifact_refused"
        print("joint-master-public=refused reason=" + reason, file=sys.stderr)
        return 1
    print("joint-master-public=" + result["ci_evidence"] +
          " files=" + str(len(result["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
