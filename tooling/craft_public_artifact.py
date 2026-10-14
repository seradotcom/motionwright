#!/usr/bin/env python3
"""Publish allowlisted original craft media, never raw Semwright owner evidence.

This is a publication boundary after independent CI verification, not a
provider execution, creative approval, source rights audit or renderer retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from cross_app_public_artifact import (
    PublicationError, SHA, require, strict_object, read_bounded, PNG_MAGIC,
)

SCHEMA = "motionwright.v05-original-craft-public-evidence/1"
FAMILIES = {
    "contrast-landscape": ("contrast-comparison/1", "contrast_comparison",
                           "CONCEPTUAL COMPARISON / NOT MEASURED EVIDENCE"),
    "contrast-portrait": ("contrast-comparison/1", "contrast_comparison",
                          "CONCEPTUAL COMPARISON / NOT MEASURED EVIDENCE"),
    "contrast-square": ("contrast-comparison/1", "contrast_comparison",
                        "CONCEPTUAL COMPARISON / NOT MEASURED EVIDENCE"),
    "quote-landscape": ("editorial-quote/1", "editorial_quote",
                        "ILLUSTRATIVE QUOTE / ATTRIBUTION NOT VERIFIED"),
    "quote-portrait": ("editorial-quote/1", "editorial_quote",
                       "ILLUSTRATIVE QUOTE / ATTRIBUTION NOT VERIFIED"),
    "quote-square": ("editorial-quote/1", "editorial_quote",
                     "ILLUSTRATIVE QUOTE / ATTRIBUTION NOT VERIFIED"),
}
MAX_PUBLIC_BYTES = 300_000_000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish(private_root: Path, output: Path, source_sha: str,
            semwright_sha: str, status: str) -> dict:
    require(bool(SHA.fullmatch(source_sha)) and bool(SHA.fullmatch(semwright_sha)),
            "source_identity_invalid")
    require(status in {"success", "failure", "cancelled", "skipped"},
            "ci_status_invalid")
    require(not output.exists() and not output.is_symlink(),
            "public_destination_already_exists")
    src, dst = private_root.resolve(), output.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "private_public_roots_overlap")
    media: dict[str, bytes] = {}
    manifest_files: list[dict] = []
    if status == "success":
        require(private_root.is_dir() and not private_root.is_symlink(),
                "private_source_missing_or_linked")
        for fixture, (family, layout_name, disclaimer) in FAMILIES.items():
            folder = private_root / source_sha / fixture
            require(folder.is_dir() and not folder.is_symlink(),
                    "private_fixture_missing_or_linked")
            result = strict_object(read_bounded(folder, "result.json", 192_000))
            inspection = strict_object(read_bounded(
                folder, "creative-frame-inspection.json", 192_000))
            project_bytes = read_bounded(folder, "editable-project.json", 2_000_000)
            project = strict_object(project_bytes)
            layout = strict_object(read_bounded(
                folder, "expected-component-layout.json", 2_000_000))
            require(
                result.get("native_av_master_e2e") == "PASS"
                and result.get("motionwright_sha") == source_sha
                and result.get("semwright_sha") == semwright_sha
                and result.get("fixture") == fixture
                and result.get("project", {}).get("fixture") == family
                and type(result.get("frame_count")) is int
                and result["frame_count"] == 180
                and type(result.get("decoded_video_frames")) is int
                and result["decoded_video_frames"] == 180
                and inspection.get("technical_sampling") == "PASS"
                and inspection.get("layout_regression") == "PASS"
                and inspection.get("creative_approval") == "required"
                and inspection.get("contact_sheet", {}).get("path")
                   == "native-contact-sheet.png"
                and inspection.get("source_project", {}).get("sha256")
                   == digest(project_bytes)
                and project.get("production_design", {}).get("heroes", [{}])[0]
                   .get("config", {}).get("layout") == layout_name
                and len(layout.get("nodes", [])) == 6
                and layout["nodes"][-1].get("text") == disclaimer,
                "unverified_craft_public_source",
            )
            sheet = read_bounded(folder, "native-contact-sheet.png", 36_000_000)
            master = read_bounded(folder, "motionwright-master.mp4", 90_000_000)
            require(sheet.startswith(PNG_MAGIC)
                    and master[4:8] == b"ftyp", "invalid_public_media")
            require(inspection["contact_sheet"]["sha256"] == digest(sheet)
                    and result.get("master_sha256") == digest(master),
                    "public_media_digest_mismatch")
            for extension, data in (("png", sheet), ("mp4", master)):
                name = fixture + "." + extension
                media[name] = data
                manifest_files.append({
                    "name": name, "fixture": fixture, "bytes": len(data),
                    "sha256": digest(data),
                })
        require(sum(map(len, media.values())) <= MAX_PUBLIC_BYTES,
                "public_media_budget_exceeded")

    receipt = {
        "schema": SCHEMA,
        "motionwright_source_sha": source_sha,
        "semwright_source_pin": semwright_sha,
        "ci_evidence": "PASS" if status == "success" else "NOT_VERIFIED",
        "included_files": manifest_files,
        "raw_owner_receipts_uploaded": False,
        "raw_native_logs_uploaded": False,
        "native_render_started_by_publication": False,
        "source_rights_verified": False,
        "human_creative_approval": "NOT_RUN",
        "v05_release_ready": False,
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(media.items()):
        with (output / name).open("xb") as stream:
            stream.write(data)
    with (output / "public-evidence.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        lock = strict_object((Path(__file__).resolve().parents[1]
                              / "SOURCE_LOCK.json").read_bytes())
        sha = lock["dependencies"]["semwright"]["revision"]
        receipt = publish(args.evidence_root, args.output_root,
                          args.source_sha, sha, args.job_status)
    except (PublicationError, OSError, ValueError, KeyError, TypeError) as exc:
        reason = exc.code if isinstance(exc, PublicationError) else "publication_refused"
        print("original-craft-public=refused reason=" + reason, file=sys.stderr)
        return 1
    print("original-craft-public=" + receipt["ci_evidence"]
          + " published=" + str(len(receipt["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
