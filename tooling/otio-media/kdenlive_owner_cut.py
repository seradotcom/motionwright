#!/usr/bin/env python3
"""Apply one authenticated disposable Kdenlive cut through the existing owner contract.

The original Kdenlive preview and files remain unchanged. A separate derived
legacy preview retains its source/media/property restrictions. This technical
CI fixture never declares human, editorial, rights or release acceptance.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from bridge import import_preview
import kdenlive_complete_g2_fixture as fixture

OWNER_EXAMPLE = fixture.ROOT / "target/debug/examples/native_otio_owner_cut_e2e"


def derive_owner_preview(baseline: dict, returned: dict, source: dict,
                         source_sha: str) -> dict:
    require = fixture.require
    scenes = source.get("scenes", [])
    ids = [scene.get("id") for scene in scenes]
    require(len(scenes) == 3 and len(set(ids)) == 3
            and all(scene.get("duration") == {"num": "1", "den": "1"}
                    for scene in scenes),
            "owner_cut_requires_original_three_one_second_scenes")
    require(baseline.get("schema") == "motionwright.otio-edit-preview/1"
            and returned.get("schema") == "motionwright.kdenlive-bound-cut-preview/1"
            and returned.get("profile") == "kdenlive-bound-video-cut-v1"
            and returned.get("status") == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
            "owner_cut_requires_existing_validated_preview_profiles")
    for name, value in (
        ("source_project_sha256", source_sha),
        ("project_id", source.get("id")),
        ("generation", source.get("generation")),
        ("expected_project_revision", source.get("revision")),
        ("original_scene_order", ids),
        ("proposed_scene_order", ids),
        ("media_refs_verified", 3),
        ("edit_imported", False),
        ("source_media_relinked", False),
        ("owner_revision_commit_required", True),
        ("human_review_required", True),
        ("semantic_native_roundtrip", "NOT_SUPPORTED"),
        ("nle_receiver_acceptance", "NOT_VERIFIED"),
        ("publication_approved", False),
    ):
        require(name in baseline and name in returned
                and type(baseline[name]) is type(value)
                and type(returned[name]) is type(value)
                and baseline[name] == value and returned[name] == value,
                "owner_cut_source_or_authority_binding_changed:" + name)
    require(baseline.get("native_change_proposals") == []
            and baseline.get("missing_references_retained") == 0
            and baseline.get("rendering_performed") is False
            and returned.get("original_missing_scene_ids") == []
            and returned.get("raw_files_rewritten") is False
            and returned.get("native_final_frames_verified") is False
            and returned.get("native_project_reopened") == "NOT_VERIFIED"
            and returned.get("received_clip_count") == 3
            and returned.get("edited_clip_count") == 3,
            "owner_cut_cannot_promote_unobserved_semantics_or_missing_media")
    expected = [{"type": "set_scene_duration", "scene_id": ids[2],
                 "duration": {"num": "1", "den": "2"}}]
    require(returned.get("native_change_proposals") == expected,
            "owner_cut_requires_exact_returned_third_scene_tail_trim")
    require(baseline.get("property_fidelity") == returned.get("property_fidelity")
            and len(baseline.get("property_fidelity", [])) == 12
            and isinstance(baseline.get("loss_report"), list)
            and len(baseline["loss_report"]) > 0
            and isinstance(returned.get("loss_report"), list)
            and returned["loss_report"][:len(baseline["loss_report"])]
                == baseline["loss_report"],
            "owner_cut_cannot_erase_source_property_losses")
    derived = copy.deepcopy(baseline)
    derived["native_change_proposals"] = copy.deepcopy(expected)
    derived["loss_report"] = copy.deepcopy(returned["loss_report"])
    return derived


def commit_once(capsule: Path, evidence: Path, database: Path,
                returned_preview_path: Path) -> dict:
    require = fixture.require
    source_path = capsule / "source-project.json"
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    returned_bytes = returned_preview_path.read_bytes()
    returned = json.loads(returned_bytes)
    baseline = import_preview(capsule, "timeline.otio", source_path)
    derived = derive_owner_preview(baseline, returned, source,
                                   fixture.sha(source_path))
    require(OWNER_EXAMPLE.is_file() and not OWNER_EXAMPLE.is_symlink()
            and database.is_file() and not database.is_symlink()
            and database.parent.resolve(strict=True) == database.parent
            and database.is_relative_to(evidence),
            "owner_cut_requires_existing_disposable_canonical_database")
    binary_sha = fixture.sha(OWNER_EXAMPLE)
    derived_path = evidence / "owner-cut-preview.json"
    receipt_path = evidence / "owner-cut-commit-receipt.json"
    fixture._new_original(
        derived_path, (json.dumps(derived, sort_keys=True, indent=2) + "\n").encode())
    fixture._new_original(
        evidence / "owner-cut-provenance.json",
        (json.dumps({
            "schema": "motionwright.native.kdenlive-owner-cut-provenance/1",
            "source_project_sha256": fixture.sha(source_path),
            "returned_Kdenlive_preview_sha256": fixture.sha(returned_preview_path),
            "derived_owner_preview_sha256": fixture.sha(derived_path),
            "owner_example_sha256": binary_sha,
            "source_raw_files_rewritten": False,
            "automated_disposable_fixture": True,
            "human_acceptance": False, "release_authorized": False,
        }, sort_keys=True, indent=2) + "\n").encode())
    # The existing one-shot caller records durable intent/terminal/streams.
    # Any exception exits this function: no commit retry or automatic recovery.
    committed = json.loads(fixture.run_firstparty_native(
        [str(OWNER_EXAMPLE), "commit", str(database), str(source_path),
         str(derived_path), str(returned_preview_path), str(receipt_path)],
        timeout=90, evidence_prefix=evidence / "owner-cut-commit-cli"))
    require(committed == json.loads(receipt_path.read_bytes())
            and committed.get("schema") == "motionwright.native.otio-owner-cut-commit/1"
            and committed.get("single_revision_committed") is True
            and committed.get("human_acceptance") is False,
            "owner_cut_original_commit_receipt_not_observed")
    reopened = json.loads(fixture.run_firstparty_native(
        [str(OWNER_EXAMPLE), "readback", str(database), str(source_path),
         str(receipt_path)], timeout=90,
        evidence_prefix=evidence / "owner-cut-readback-cli"))
    require(reopened.get("schema") == "motionwright.native.otio-owner-cut-readback/1"
            and reopened.get("project_reopened_equal") is True
            and reopened.get("exact_one_history_event") is True
            and reopened.get("verified_source_blob_retained") is True
            and reopened.get("canonical_project_mutation_performed") is False
            and reopened.get("human_acceptance") is False
            and reopened.get("release_authorized") is False
            and fixture.sha(OWNER_EXAMPLE) == binary_sha
            and source_path.read_bytes() == source_bytes
            and returned_preview_path.read_bytes() == returned_bytes,
            "owner_cut_reopen_or_immutable_input_evidence_changed")
    fixture._new_original(
        evidence / "owner-cut-reopen-readback.json",
        (json.dumps(reopened, sort_keys=True, indent=2) + "\n").encode())
    return {
        "sqlite_owner_cut_committed": True,
        "single_revision_increment": 1,
        "new_process_project_reopened_equal": True,
        "exact_one_history_event": True,
        "verified_source_blob_retained": True,
        "owner_cut_commit_receipt_sha256": fixture.sha(receipt_path),
        "owner_cut_reopen_receipt_sha256":
            fixture.sha(evidence / "owner-cut-reopen-readback.json"),
        "automated_disposable_fixture": True,
        "human_acceptance": False, "release_authorized": False,
    }
