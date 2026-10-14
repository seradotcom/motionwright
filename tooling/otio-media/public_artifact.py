#!/usr/bin/env python3
"""Curate ONLY the synthetic OTIO cut and matching original H.264 CI fixture.

A raw portable bundle can contain source-owner information and must never be
uploaded wholesale. No NLE execution, user rights acceptance or edit admission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cross_app_public_artifact import (  # noqa: E402
    PRIVATE_TEXT, PublicationError, SHA, read_bounded, require, strict_object,
)

SCHEMA = "motionwright.v05-otio-synthetic-public/1"
MEDIA_SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_MP4 = 32_000_000


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def publish(private: Path, public: Path, sha: str, status: str) -> dict:
    require(bool(SHA.fullmatch(sha)), "source_sha_invalid")
    require(status in {"success", "failure", "cancelled", "skipped"}, "ci_status_invalid")
    require(not public.exists() and not public.is_symlink(), "existing_public_destination")
    src, dst = private.resolve(), public.resolve()
    require(src != dst and src not in dst.parents and dst not in src.parents,
            "private_and_public_overlap")
    files: dict[str, bytes] = {}
    if status == "success":
        require(private.is_dir() and not private.is_symlink(), "missing_private_root")
        result = strict_object(read_bounded(private, "result.json", 128_000))
        bundle = private / "portable"
        require(bundle.is_dir() and not bundle.is_symlink(), "missing_portable_source")
        require(not (bundle / "media").is_symlink(), "linked_media_directory")
        project_bytes = read_bounded(bundle, "source-project.json", 3_000_000)
        project = strict_object(project_bytes)
        manifest = strict_object(read_bounded(bundle, "bridge-manifest.json", 512_000))
        timeline_bytes = read_bounded(bundle, "timeline.otio", 2_000_000)
        timeline = strict_object(timeline_bytes)
        digest = result.get("media_sha256")
        require(
            result.get("schema") == "motionwright.otio-real-media-native-e2e/1"
            and result.get("motionwright_sha") == sha
            and result.get("nle_receiver") == "opentimelineio_python_schema"
            and result.get("real_video_stream_decode_and_rational_time") == "PASS"
            and result.get("source_references_located") == 2
            and result.get("MissingReference_preserved") == 1
            and result.get("bounded_cut_import_preview") == "PASS_NOT_APPLIED"
            and result.get("audio_and_native_effects") == "NOT_ROUNDTRIPPED"
            and result.get("external_nle_editor") == "NOT_RUN"
            and result.get("human_creative_approval") == "NOT_REVIEWED"
            and result.get("native_editing_changes_committed") is False
            and result.get("release") == "BLOCKED_REVIEW"
            and isinstance(digest, str) and bool(MEDIA_SHA.fullmatch(digest))
            and project.get("title") == "Original source media editorial exchange"
            and [s.get("name") for s in project.get("scenes", [])] ==
                ["Opening source", "Detail source", "Abstract branded CTA"]
            and len(project.get("assets", [])) == 1
            and project["assets"][0].get("content_sha256") == digest
            and hash_bytes(project_bytes) == result.get("source_project_sha256")
            and manifest.get("project_sha256") == result.get("source_project_sha256")
            and manifest.get("source_media_count") == 1
            and manifest.get("nle_import_verified") is False
            and manifest.get("owner_review_declared") == "CI original fixture owner"
            and len(manifest.get("property_fidelity", [])) == 12,
            "unverified_synthetic_otio_source",
        )
        meta = timeline.get("metadata", {}).get("motionwright", {})
        children = timeline.get("tracks", {}).get("children", [])
        require(timeline.get("OTIO_SCHEMA") == "Timeline.1" and
                len(children) == 1 and isinstance(children[0].get("children"), list)
                and len(children[0]["children"]) == 3 and
                meta.get("permission_to_publish") is False and
                meta.get("native_editable_reimport_supported") is False and
                len(meta.get("property_fidelity", [])) == 12 and
                meta.get("verified_media_clip_count") == 2 and
                meta.get("source_project_sha256") == result["source_project_sha256"],
                "unsafe_synthetic_otio_timeline")
        clips = children[0]["children"]
        require(all(c.get("OTIO_SCHEMA") == "Clip.1" for c in clips)
                and all(c.get("media_reference", {}).get("OTIO_SCHEMA") == "ExternalReference.1"
                        and c["media_reference"].get("target_url") == f"media/{digest}.mp4"
                        for c in clips[:2])
                and clips[2].get("media_reference", {}).get("OTIO_SCHEMA") ==
                    "MissingReference.1"
                and PRIVATE_TEXT.search(timeline_bytes.decode("utf-8")) is None,
                "unsafe_synthetic_otio_media_reference")
        media = read_bounded(bundle, f"media/{digest}.mp4", MAX_MP4)
        require(media[4:8] == b"ftyp" and hash_bytes(media) == digest,
                "synthetic_media_digest_mismatch")
        files = {"timeline.otio": timeline_bytes, f"media/{digest}.mp4": media}

    receipts = [
        {"name": name, "sha256": hash_bytes(data), "bytes": len(data)}
        for name, data in sorted(files.items())
    ]
    report = {
        "schema": SCHEMA, "motionwright_source_sha": sha,
        "ci_evidence": "PASS" if status == "success" else "NOT_VERIFIED",
        "included_files": receipts,
        "synthetic_first_party_fixture_only": True,
        "owner_project_uploaded": False,
        "owner_media_receipts_uploaded": False,
        "real_human_rights_authenticated": False,
        "external_nle_application_tested": False,
        "native_edits_committed": False,
        "source_claims_published_as_verified": False,
        "human_acceptance": "NOT_RUN",
        "v05_release_ready": False,
    }
    public.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(files.items()):
        dest = public / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as f:
            f.write(data)
    with (public / "public-evidence.json").open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--job-status", required=True)
    args = parser.parse_args()
    try:
        result = publish(args.evidence_root, args.public_root, args.source_sha,
                         args.job_status)
    except (PublicationError, ValueError, OSError, KeyError, TypeError) as error:
        reason = error.code if isinstance(error, PublicationError) else "public_otio_refused"
        print("otio-public=refused reason=" + reason, file=sys.stderr)
        return 1
    print("otio-public=" + result["ci_evidence"] + " files=" +
          str(len(result["included_files"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
