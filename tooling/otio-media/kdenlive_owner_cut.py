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

from bridge import capture_original, import_preview
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
    source_bytes, source_observed = capture_original(source_path, 2 * 1024 * 1024)
    source = json.loads(source_bytes)
    returned_bytes, returned_observed = capture_original(returned_preview_path, 2 * 1024 * 1024)
    returned = json.loads(returned_bytes)
    baseline = import_preview(capsule, "timeline.otio", source_path)
    derived = derive_owner_preview(baseline, returned, source,
                                   source_observed["sha256"])
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
            "source_project_sha256": source_observed["sha256"],
            "returned_Kdenlive_preview_sha256": returned_observed["sha256"],
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
    receipt_bytes, receipt_observed = capture_original(receipt_path, 2 * 1024 * 1024)
    require(committed == json.loads(receipt_bytes)
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
            and capture_original(source_path, 2 * 1024 * 1024)[0] == source_bytes
            and capture_original(returned_preview_path, 2 * 1024 * 1024)[0] == returned_bytes
            and capture_original(receipt_path, 2 * 1024 * 1024)[0] == receipt_bytes,
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
        "owner_cut_commit_receipt_sha256": receipt_observed["sha256"],
        "owner_cut_reopen_receipt_sha256":
            fixture.sha(evidence / "owner-cut-reopen-readback.json"),
        "automated_disposable_fixture": True,
        "human_acceptance": False, "release_authorized": False,
    }

def source_cycle_once(capsule: Path, evidence: Path, database: Path,
                      native_path: Path, native_sha: str,
                      owner_completion: dict) -> dict:
    """Attach the observed original, then use the existing inspected bundle APIs.

    These are separate, one-shot operations after the original owner cut and its
    readback. An exception stops the sequence; there is no replay or recovery call.
    """
    from kdenlive_readback import read_closed_bytes
    from bridge import fingerprint
    import hashlib
    require = fixture.require
    require(type(owner_completion) is dict
            and owner_completion.get("sqlite_owner_cut_committed") is True
            and owner_completion.get("new_process_project_reopened_equal") is True
            and type(owner_completion.get("single_revision_increment")) is int
            and owner_completion["single_revision_increment"] == 1
            and owner_completion.get("human_acceptance") is False
            and owner_completion.get("release_authorized") is False,
            "native_source_cycle_requires_the_observed_original_owner_cut")
    source_path = capsule / "source-project.json"
    cut_receipt = evidence / "owner-cut-commit-receipt.json"
    cut_bytes = read_closed_bytes(
        cut_receipt, owner_completion.get("owner_cut_commit_receipt_sha256"), 2 * 1024 * 1024)
    cut = json.loads(cut_bytes)
    require(type(cut) is dict
            and cut.get("schema") == "motionwright.native.otio-owner-cut-commit/1"
            and cut.get("single_revision_committed") is True
            and cut.get("human_acceptance") is False
            and cut.get("release_authorized") is False,
            "native_source_cycle_owner_receipt_schema_changed")
    source_bytes = read_closed_bytes(
        source_path, cut.get("source_project_sha256"), 2 * 1024 * 1024)
    original_project = cut.get("committed_project")
    require(type(original_project) is dict
            and type(original_project.get("id")) is str and original_project["id"]
            and type(original_project.get("generation")) is str and original_project["generation"]
            and type(original_project.get("revision")) is int
            and original_project["revision"] >= 0,
            "native_source_cycle_original_owner_project_not_bound")
    require(native_path == evidence / "edited.kdenlive",
            "native_source_cycle_requires_the_closed_observed_native_member")
    native_bytes = read_closed_bytes(native_path, native_sha, 5 * 1024 * 1024)
    require(native_bytes and OWNER_EXAMPLE.is_file() and not OWNER_EXAMPLE.is_symlink()
            and database.is_file() and not database.is_symlink()
            and database.parent.resolve(strict=True) == database.parent
            and database.is_relative_to(evidence),
            "native_source_cycle_originals_or_disposable_database_unavailable")
    binary_sha = fixture.sha(OWNER_EXAMPLE)
    attached_path = evidence / "native-source-attach-receipt.json"
    reopened_path = evidence / "native-source-reopen.json"
    bundle = evidence / "native-source-portable"
    restored_owner = evidence / "native-source-restored-owner"
    restored_database = restored_owner / "motionwright.sqlite3"
    bundle_receipt = evidence / "native-source-bundle-receipt.json"
    portable_readback_path = evidence / "native-source-portable-reopen.json"
    fixture._new_original(evidence / "native-source-cycle-input.original.json",
        (json.dumps({
            "schema": "motionwright.native.kdenlive-source-cycle-input/1",
            "owner_cut_receipt_sha256": owner_completion["owner_cut_commit_receipt_sha256"],
            "source_project_sha256": cut["source_project_sha256"],
            "native_source_sha256": native_sha, "native_source_bytes": len(native_bytes),
            "owner_example_sha256": binary_sha,
            "native_editor_dispatched_here": False, "automatic_retry": False,
            "human_acceptance": False, "release_authorized": False,
        }, sort_keys=True, indent=2) + "\n").encode())

    def read_receipt(path):
        original, observed = capture_original(path, 2 * 1024 * 1024)
        return json.loads(original), observed["sha256"], original

    def call(mode, arguments, prefix):
        # The existing helper retains original argv, streams and terminal before
        # this JSON decode. This wrapper neither owns nor replaces an executor.
        return json.loads(fixture.run_firstparty_native(
            [str(OWNER_EXAMPLE), mode, *(str(value) for value in arguments)],
            timeout=90, evidence_prefix=evidence / prefix))

    attached = call("source-attach", (
        database, source_path, cut_receipt, native_path, native_sha, attached_path),
        "native-source-attach-cli")
    attached_original, attached_sha, attached_bytes = read_receipt(attached_path)
    require(type(attached) is dict
            and attached == attached_original
            and attached.get("schema") == "motionwright.native.kdenlive-source-import/1"
            and attached.get("request_id") == "original:kdenlive-capsule:" + native_sha
            and attached.get("source_project_sha256") == cut["source_project_sha256"]
            and attached.get("owner_cut_receipt_sha256") ==
                owner_completion["owner_cut_commit_receipt_sha256"]
            and attached.get("native_source_sha256") == native_sha
            and attached.get("single_source_revision_committed") is True
            and attached.get("source_fidelity_unavailable") is True
            and attached.get("source_blob_reopened_equal") is True
            and attached.get("native_editor_dispatched_here") is False
            and attached.get("per_scene_native_semantic_mapping_verified") is False
            and attached.get("automatic_retry") is False
            and attached.get("human_acceptance") is False
            and attached.get("release_authorized") is False,
            "native_source_cycle_original_import_result_not_bound")
    attached_project = attached.get("committed_project")
    require(type(original_project) is dict and type(attached_project) is dict
            and attached_project.get("id") == original_project.get("id")
            and attached_project.get("generation") == original_project.get("generation")
            and type(original_project.get("revision")) is int
            and type(attached_project.get("revision")) is int
            and attached_project["revision"] == original_project["revision"] + 1,
            "native_source_cycle_did_not_add_one_current_source_revision")
    reopened = call("source-readback",
        (database, source_path, cut_receipt, attached_path), "native-source-readback-cli")
    require(type(reopened) is dict
            and reopened.get("schema") == "motionwright.native.kdenlive-source-readback/1"
            and reopened.get("attachment_receipt_sha256") == attached_sha
            and reopened.get("committed_project_sha256") == attached.get("committed_project_sha256")
            and reopened.get("native_source_sha256") == native_sha
            and reopened.get("project_id") == attached_project["id"]
            and reopened.get("generation") == attached_project["generation"]
            and reopened.get("revision") == attached_project["revision"]
            and all(reopened.get(key) is True for key in (
                "project_reopened_equal", "source_blob_reopened_equal",
                "source_fidelity_unavailable", "exact_two_history_events"))
            and all(reopened.get(key) is False for key in (
                "canonical_project_mutation_performed", "native_editor_dispatched_here",
                "automatic_retry", "human_acceptance", "release_authorized")),
            "native_source_cycle_readback_did_not_reopen_exact_attached_source")
    reopened_bytes = (json.dumps(reopened, sort_keys=True, indent=2) + "\n").encode()
    reopened_sha = hashlib.sha256(reopened_bytes).hexdigest()
    fixture._new_original(reopened_path, reopened_bytes)
    # A separate new directory keeps the destination storage independent from
    # the source database's adjacent CAS. Existing directories are never reused.
    restored_owner.mkdir(mode=0o700)
    portable = call("source-bundle", (
        database, source_path, cut_receipt, attached_path, bundle,
        restored_database, bundle_receipt), "native-source-bundle-cli")
    portable_original, portable_sha, portable_bytes = read_receipt(bundle_receipt)
    require(type(portable) is dict
            and portable == portable_original
            and portable.get("schema") == "motionwright.native.kdenlive-source-portability/1"
            and portable.get("attachment_receipt_sha256") == attached_sha
            and portable.get("native_source_sha256") == native_sha
            and portable.get("project_id") == attached_project["id"]
            and portable.get("source_generation") == attached_project["generation"]
            and type(portable.get("imported_generation")) is str
            and portable["imported_generation"] != attached_project["generation"]
            and portable.get("revision") == attached_project["revision"]
            and type(portable.get("production_receipt_count")) is int
            and portable["production_receipt_count"] == 0
            and all(portable.get(key) is True for key in (
                "fresh_generation_observed", "source_assets_and_capsules_preserved",
                "source_blob_reopened_equal", "source_fidelity_unavailable"))
            and all(portable.get(key) is False for key in (
                "local_production_receipts_inherited", "execution_authority_issued",
                "native_editor_dispatched_here", "automatic_retry",
                "human_acceptance", "release_authorized"))
            and type(portable.get("inspection_fingerprint")) is str
            and len(portable["inspection_fingerprint"]) == 64
            and all(value in "0123456789abcdef" for value in portable["inspection_fingerprint"])
            and type(portable.get("bundle_manifest_sha256")) is str
            and len(portable["bundle_manifest_sha256"]) == 64
            and all(value in "0123456789abcdef" for value in portable["bundle_manifest_sha256"]),
            "native_source_cycle_portable_import_not_bound")
    manifest_bytes = read_closed_bytes(
        bundle / "manifest.json", portable["bundle_manifest_sha256"], 2 * 1024 * 1024)
    portable_reopened = call("source-portable-readback",
        (restored_database, bundle_receipt), "native-source-portable-readback-cli")
    require(type(portable_reopened) is dict
            and portable_reopened.get("schema") == "motionwright.native.kdenlive-source-portable-readback/1"
            and portable_reopened.get("bundle_receipt_sha256") == portable_sha
            and portable_reopened.get("native_source_sha256") == native_sha
            and portable_reopened.get("project_id") == attached_project["id"]
            and portable_reopened.get("generation") == portable["imported_generation"]
            and portable_reopened.get("revision") == attached_project["revision"]
            and portable_reopened.get("imported_project_sha256") ==
                portable.get("imported_project_sha256")
            and type(portable_reopened.get("production_receipt_count")) is int
            and portable_reopened["production_receipt_count"] == 0
            and all(portable_reopened.get(key) is True for key in (
                "project_reopened_equal", "source_blob_reopened_equal",
                "fresh_generation_observed", "source_fidelity_unavailable"))
            and all(portable_reopened.get(key) is False for key in (
                "local_production_receipts_inherited", "canonical_project_mutation_performed",
                "execution_authority_issued", "native_editor_dispatched_here",
                "automatic_retry", "human_acceptance", "release_authorized")),
            "native_source_cycle_destination_readback_not_bound")
    portable_reopened_bytes = (json.dumps(portable_reopened, sort_keys=True, indent=2) + "\n").encode()
    portable_reopened_sha = hashlib.sha256(portable_reopened_bytes).hexdigest()
    fixture._new_original(portable_readback_path, portable_reopened_bytes)
    require(fixture.sha(OWNER_EXAMPLE) == binary_sha
            and read_closed_bytes(native_path, native_sha, 5 * 1024 * 1024) == native_bytes
            and read_closed_bytes(source_path, cut["source_project_sha256"],
                                  2 * 1024 * 1024) == source_bytes
            and read_closed_bytes(cut_receipt,
                owner_completion["owner_cut_commit_receipt_sha256"], 2 * 1024 * 1024) == cut_bytes
            and read_closed_bytes(attached_path, attached_sha, 2 * 1024 * 1024) == attached_bytes
            and read_closed_bytes(reopened_path, reopened_sha, 2 * 1024 * 1024) == reopened_bytes
            and read_closed_bytes(bundle_receipt, portable_sha, 2 * 1024 * 1024) == portable_bytes
            and read_closed_bytes(portable_readback_path, portable_reopened_sha,
                                  2 * 1024 * 1024) == portable_reopened_bytes
            and read_closed_bytes(bundle / "manifest.json", portable["bundle_manifest_sha256"],
                                  2 * 1024 * 1024) == manifest_bytes,
            "native_source_cycle_originals_changed_after_portable_readback")
    return {
        "native_source_import_committed": True, "additional_source_revision_increment": 1,
        "source_readback_in_new_process": True, "source_blob_reopened_equal": True,
        "portable_readback_in_new_process": True, "fresh_generation_observed": True,
        "source_fidelity_unavailable": True, "native_source_sha256": native_sha,
        "source_generation": attached_project["generation"],
        "portable_generation": portable["imported_generation"],
        "native_source_import_receipt_sha256": attached_sha,
        "native_source_readback_sha256": reopened_sha,
        "native_source_bundle_receipt_sha256": portable_sha,
        "native_source_portable_readback_sha256": portable_reopened_sha,
        "bundle_manifest_sha256": portable["bundle_manifest_sha256"],
        "bundle_inspection_fingerprint": portable["inspection_fingerprint"],
        "production_receipt_count": 0, "local_production_receipts_inherited": False,
        "execution_authority_issued": False, "per_scene_native_semantic_mapping_verified": False,
        "native_editor_dispatched_here": False, "alab_sources_adopted_here": False,
        "automatic_retry": False, "human_acceptance": False, "release_authorized": False,
    }


_ALAB_SOURCE_NOTICES = (
    ("license", "ALab-LICENSE.original.txt", 2622,
     "4263d9c4ad26b95bda3398c406ef9787f696f236fb88ab509693371983174021"),
    ("readme", "ALab-README.original.txt", 8729,
     "e5964177d9826d6f80c3318a01e3404d3bc2f24d8cdd143a4b566b0b3ff295a0"),
    ("selection_notice", "ALab-SELECTION-NOTICE.original.txt", 902,
     "0d90946e0b723a499bf516275d3407100d022de9c76935bcc6fa368f70daa376"),
)




def _freeze_owner_call_originals(prefix: Path, argv: list[str], output: bytes, pinned: dict) -> None:
    """Bind the existing helper's original files before interpreting stdout.

    A real empty captured stream is distinct from an absent member. This uses
    the existing private-custody reader and the same process result, not a new
    runner or recapture of the native operation.
    """
    import ci_private_evidence as custody
    require = fixture.require
    require(type(output) is bytes and type(argv) is list and all(type(value) is str for value in argv),
            "alab_owner_call_original_result_not_binary")
    original = {}
    for suffix, maximum in ((".intent.json", 2 * 1024 * 1024), (".terminal.json", 2 * 1024 * 1024),
                            (".stdout.bin", fixture.MAX_RUST_STDOUT), (".stderr.bin", 32 * 1024)):
        path = prefix.with_name(prefix.name + suffix)
        require(path not in pinned, "alab_owner_call_original_path_reused")
        raw, _ = custody.regular(path, maximum)
        original[suffix] = (path, raw, custody.sha(raw))
    require(original[".stdout.bin"][1] == output,
            "alab_owner_stdout_file_differs_from_the_same_original_process_result")
    intent = custody.strict(original[".intent.json"][1])
    terminal = custody.strict(original[".terminal.json"][1])
    require(type(intent) is dict and set(intent) == {"state", "command", "timeout", "invocation_retried"}
            and intent["state"] == "INTENT" and intent["command"] == argv
            and type(intent["timeout"]) is int and intent["timeout"] == 90
            and intent["invocation_retried"] is False,
            "alab_owner_call_intent_differs_from_the_original_argv")
    require(type(terminal) is dict and set(terminal) == {"schema", "state", "returncode", "invocation_retried",
                "timeout_policy", "error", "captured_streams"}
            and terminal["schema"] == "motionwright.native.firstparty-source-call/1"
            and terminal["state"] == "TERMINAL_OBSERVED"
            and type(terminal["returncode"]) is int and terminal["returncode"] == 0
            and terminal["invocation_retried"] is False and terminal["error"] is None
            and terminal["timeout_policy"] == "existing_subprocess_run"
            and type(terminal["captured_streams"]) is dict and set(terminal["captured_streams"]) == {"stdout", "stderr"},
            "alab_owner_call_terminal_not_the_original_success")
    for stream in ("stdout", "stderr"):
        _, raw, digest = original["." + stream + ".bin"]
        row = terminal["captured_streams"][stream]
        require(type(row) is dict and set(row) == {"captured", "bytes", "sha256"}
                and row["captured"] is True and type(row["bytes"]) is int and row["bytes"] == len(raw)
                and row["sha256"] == digest,
                "alab_owner_call_original_stream_does_not_match_its_terminal")
    for path, raw, digest in original.values():
        pinned[path] = (digest, raw)


def _owner_call_originals_unchanged(pinned: dict) -> None:
    import ci_private_evidence as custody
    for path, (digest, original) in pinned.items():
        raw, _ = custody.regular(path, len(original))
        fixture.require(raw == original and custody.sha(raw) == digest,
                        "alab_owner_call_original_changed_after_its_first_observation")


def _owner_call_original_bindings(pinned: dict, root: Path) -> dict:
    return {path.relative_to(root).as_posix(): {"bytes": len(raw), "sha256": digest}
            for path, (digest, raw) in pinned.items()}


def prepare_alab_original(destination: Path, *, evidence_root: Path,
                          source_database: Path, originals: dict, notices: dict,
                          owner_review: dict, media_rights: list) -> tuple:
    """Adopt the two fixed originals through Service, then prepare the existing cut.

    This is a new explicit benchmark profile. The default generated three-scene
    fixture is unchanged. The retained observation supplies media facts, never
    an owner grant or a receiver verdict; redistribution declarations remain
    explicit inputs from the admitted caller.
    """
    import hashlib
    import bridge as media_bridge
    from kdenlive_readback import read_closed_bytes
    require = fixture.require
    roles = ("otio", "readback", "container0", "container1", "media0", "media1")
    require(type(originals) is dict and set(originals) == set(roles)
            and all(isinstance(originals[role], Path) for role in roles),
            "alab_original_roles_must_be_closed_and_explicit")
    require(type(notices) is dict
            and set(notices) == {row[0] for row in _ALAB_SOURCE_NOTICES}
            and all(isinstance(path, Path) for path in notices.values()),
            "alab_requires_the_three_original_license_readme_selection_members")
    require(type(owner_review) is dict
            and set(owner_review) == {"reviewer", "synthetic_source_allowed"}
            and type(owner_review["reviewer"]) is str
            and 1 <= len(owner_review["reviewer"].strip()) <= 200
            and owner_review["synthetic_source_allowed"] is False,
            "alab_requires_explicit_original_source_review_not_a_synthetic_fixture")
    require(type(media_rights) is list and len(media_rights) == 2,
            "alab_requires_two_explicit_original_source_rights_declarations")
    for declaration in media_rights:
        require(type(declaration) is dict
                and set(declaration) == {"owner", "license", "authorized", "redistributable"}
                and declaration["authorized"] is True
                and declaration["redistributable"] is True
                and all(type(declaration[key]) is str
                        and 1 <= len(declaration[key].strip()) <= 250
                        for key in ("owner", "license"))
                and "synthetic" not in declaration["license"].lower(),
                "alab_original_copy_requires_the_callers_explicit_rights_declaration")
    owner_review = copy.deepcopy(owner_review)
    media_rights = copy.deepcopy(media_rights)
    temp = fixture.require_ephemeral_approved_source(destination)
    require(source_database.is_absolute()
            and source_database.parent == evidence_root.parent
            and source_database.is_relative_to(temp)
            and source_database.parent.resolve(strict=True) == source_database.parent
            and not source_database.exists() and not source_database.is_symlink()
            and OWNER_EXAMPLE.is_file() and not OWNER_EXAMPLE.is_symlink()
            and all(path.is_absolute() and path.is_relative_to(temp)
                    for path in (*originals.values(), *notices.values())),
            "alab_native_source_requires_fresh_owned_paths_and_the_existing_example")
    pins = [(role, size, digest) for role, size, digest
            in media_bridge._ALAB_RETAINED_ORIGINALS]
    pins += [("media" + str(index), row[2], row[3])
             for index, row in enumerate(media_bridge._ALAB_RETAINED_MEDIA)]
    raw = {}
    for role, size, digest in pins:
        raw[role] = read_closed_bytes(originals[role], digest, size)
        require(len(raw[role]) == size, "alab_original_size_changed:" + role)
    notice_raw = {}
    for role, name, size, digest in _ALAB_SOURCE_NOTICES:
        notice_raw[role] = read_closed_bytes(notices[role], digest, size)
        require(len(notice_raw[role]) == size, "alab_original_notice_size_changed:" + role)
    retained = media_bridge.admit_alab_retained_originals(
        raw["otio"], raw["readback"], raw["container0"], raw["container1"])
    for index in range(2):
        retained.probe(originals["media" + str(index)])
    descriptor = retained.descriptor()
    derived_bytes = (json.dumps(descriptor, sort_keys=True, indent=2) + "\n").encode()
    binary_sha = fixture.sha(OWNER_EXAMPLE)
    # The new workspace is retained by the existing caller on every outcome.
    # No cleanup, rerun or replacement Host is introduced here.
    with fixture.firstparty_workspace(temp, evidence_root) as root:
        names = ("ALab_mk020_final_edit.h264.otio", "NATIVE_READBACK.original.json",
                 "mk020_0010-container.original.json", "mk020_0020-container.original.json",
                 "mk020_0010.mov", "mk020_0020.mov")
        copied = {role: root / name for role, name in zip(roles, names)}
        for role in roles:
            fixture._new_original(copied[role], raw[role])
        for role, name, _, _ in _ALAB_SOURCE_NOTICES:
            fixture._new_original(root / name, notice_raw[role])
        notice_records = [{"role": role, "file": name, "bytes": size, "sha256": digest}
                          for role, name, size, digest in _ALAB_SOURCE_NOTICES]
        derived_path = root / "alab-retained-origin-derived.json"
        fixture._new_original(derived_path, derived_bytes)
        adoption_path = root / "alab-adoption-receipt.json"
        reopen_path = root / "alab-source-reopen.json"
        bundle = root / "alab-source-portable"
        restored_owner = root / "alab-restored-owner"
        restored_database = restored_owner / "motionwright.sqlite3"
        bundle_receipt = root / "alab-bundle-receipt.json"
        portable_readback_path = root / "alab-portable-reopen.json"
        original_hashes = {role: {"sha256": digest, "bytes": size}
                           for role, size, digest in pins}
        fixture._new_original(root / "alab-source-input.original.json", (json.dumps({
            "schema": "motionwright.native.alab-source-input/1",
            "profile": media_bridge.ALAB_RETAINED_PROFILE,
            "originals": original_hashes, "original_notices": notice_records,
            "derived_descriptor_sha256": hashlib.sha256(derived_bytes).hexdigest(),
            "owner_example_sha256": binary_sha,
            "owner_review": owner_review, "media_rights": media_rights,
            "rights_are_caller_declarations_not_media_observation": True,
            "historical_readback_rewritten": False, "new_decode": False,
            "native_editor_dispatched_here": False, "automatic_retry": False,
            "human_acceptance": False, "release_authorized": False,
        }, sort_keys=True, indent=2) + "\n").encode())
        pinned_outputs, call_originals = {}, {}

        def remember(path, expected_value=None):
            data, observed = capture_original(path, 2 * 1024 * 1024)
            digest = observed["sha256"]
            value = media_bridge.strict_json(data, "original ALab source receipt")
            require(expected_value is None or value == expected_value,
                    "alab_stdout_and_retained_original_receipt_differ")
            pinned_outputs[path] = (digest, data)
            return value, digest

        def unchanged():
            _owner_call_originals_unchanged(call_originals)
            require(fixture.sha(OWNER_EXAMPLE) == binary_sha,
                    "alab_native_example_changed_between_calls")
            for role, size, digest in pins:
                require(read_closed_bytes(originals[role], digest, size) == raw[role]
                        and read_closed_bytes(copied[role], digest, size) == raw[role],
                        "alab_original_or_owned_copy_changed:" + role)
            for role, name, size, digest in _ALAB_SOURCE_NOTICES:
                require(read_closed_bytes(notices[role], digest, size) == notice_raw[role]
                        and read_closed_bytes(root / name, digest, size) == notice_raw[role],
                        "alab_original_notice_changed:" + role)
            require(read_closed_bytes(derived_path, hashlib.sha256(derived_bytes).hexdigest(),
                                      2 * 1024 * 1024) == derived_bytes,
                    "alab_derived_original_changed_between_calls")
            for path, (digest, data) in pinned_outputs.items():
                require(read_closed_bytes(path, digest, 2 * 1024 * 1024) == data,
                        "alab_retained_receipt_changed_between_calls")

        def call(mode, arguments, prefix):
            unchanged()
            # Same original process helper: stdout/err/argv/terminal are retained
            # before this strict parse. UNKNOWN exits; this loop never retries.
            argv = [str(OWNER_EXAMPLE), mode, *(str(value) for value in arguments)]
            output = fixture.run_firstparty_native(argv, timeout=90, evidence_prefix=root / prefix)
            _freeze_owner_call_originals(root / prefix, argv, output, call_originals)
            return media_bridge.strict_json(output, "original ALab native result")

        adopted = call("alab-adopt", (source_database, copied["otio"],
            copied["media0"], copied["media1"], copied["readback"], copied["container0"],
            copied["container1"], derived_path, adoption_path), "alab-adopt-cli")
        _, adoption_sha = remember(adoption_path, adopted)
        project = adopted.get("project")
        require(type(project) is dict
                and media_bridge.UUID.fullmatch(str(project.get("id", ""))) is not None
                and media_bridge.UUID.fullmatch(str(project.get("generation", ""))) is not None
                and type(project.get("revision")) is int and project["revision"] >= 7
                and adopted.get("schema") == "motionwright.native.alab-selected-source-adoption/1"
                and adopted.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
                and adopted.get("originals") == descriptor["originals"]
                and media_bridge.HEX.fullmatch(str(adopted.get("project_value_sha256", ""))) is not None
                and adopted.get("derived_descriptor_sha256") == hashlib.sha256(derived_bytes).hexdigest()
                and type(adopted.get("original_history_events")) is int
                and adopted["original_history_events"] == 7
                and type(adopted.get("production_receipt_count")) is int
                and adopted["production_receipt_count"] == 0
                and all(adopted.get(key) is True for key in (
                    "canonical_source_adopted", "sqlite_source_reopened_equal",
                    "whole_original_otio_retained", "source_fidelity_unavailable"))
                and all(adopted.get(key) is False for key in (
                    "historical_readback_rewritten", "new_decode", "new_render", "nle_opened",
                    "native_editor_dispatched_here", "execution_authority_issued",
                    "automatic_retry", "human_acceptance", "release_authorized")),
                "alab_native_adoption_result_did_not_keep_the_closed_source_scope")
        source_assets = adopted.get("source_assets")
        require(type(source_assets) is dict and type(source_assets.get("media")) is list
                and len(source_assets["media"]) == 2
                and source_assets.get("source_sha256") == original_hashes["otio"]["sha256"]
                and source_assets.get("source_bytes") == original_hashes["otio"]["bytes"]
                and source_assets.get("all_source_blobs_reopened_equal") is True
                and source_assets.get("source_fidelity_unavailable") is True
                and source_assets.get("original_unselected_references_retained_in_opaque_source") is True
                and source_assets.get("per_scene_native_editability_verified") is False,
                "alab_adoption_did_not_bind_originals_to_canonical_assets")
        stages = adopted.get("stage_receipts")
        require(type(stages) is list and len(stages) == 7,
                "alab_original_seven_stage_roster_is_incomplete")
        for index, entry in enumerate(stages):
            path = adoption_path.with_suffix(f".stage-{index:02}.result.original.json")
            require(type(entry) is dict and type(entry.get("index")) is int and entry["index"] == index
                    and entry.get("file") == path.name,
                    "alab_original_stage_path_is_not_closed")
            body = read_closed_bytes(path, entry.get("sha256"), 2 * 1024 * 1024)
            require(type(entry.get("bytes")) is int and entry["bytes"] == len(body),
                    "alab_original_stage_size_changed")
            pinned_outputs[path] = (entry["sha256"], body)
        reopened = call("alab-source-readback", (source_database, adoption_path),
                        "alab-source-readback-cli")
        require(reopened.get("schema") == "motionwright.native.alab-selected-source-readback/1"
                and reopened.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
                and reopened.get("adoption_receipt_sha256") == adoption_sha
                and reopened.get("project_id") == project["id"]
                and reopened.get("generation") == project["generation"]
                and reopened.get("revision") == project["revision"]
                and reopened.get("project_value_sha256") == adopted.get("project_value_sha256")
                and reopened.get("source_assets") == source_assets
                and all(reopened.get(key) is True for key in (
                    "project_reopened_equal", "source_fidelity_unavailable"))
                and all(reopened.get(key) is False for key in (
                    "canonical_project_mutation_performed", "execution_authority_issued",
                    "native_editor_dispatched_here", "human_acceptance", "release_authorized")),
                "alab_independent_source_readback_did_not_match_its_adoption")
        fixture._new_original(reopen_path, (json.dumps(reopened, sort_keys=True, indent=2) + "\n").encode())
        _, reopen_sha = remember(reopen_path, reopened)
        # An explicit intent precedes creating the new destination owner. This
        # parent must exist before the Rust owned_path(false) admission executes.
        fixture._new_original(root / "alab-restored-owner.intent.json", (json.dumps({
            "state": "INTENT", "destination": str(restored_owner),
            "adoption_receipt_sha256": adoption_sha, "automatic_retry": False,
        }, sort_keys=True) + "\n").encode())
        restored_owner.mkdir(mode=0o700)
        portable = call("alab-source-bundle", (source_database, adoption_path, bundle,
                         restored_database, bundle_receipt), "alab-source-bundle-cli")
        _, portability_sha = remember(bundle_receipt, portable)
        imported = portable.get("imported_project")
        require(type(imported) is dict and imported.get("id") == project["id"]
                and type(imported.get("generation")) is str
                and media_bridge.UUID.fullmatch(imported["generation"]) is not None
                and imported["generation"] != project["generation"]
                and imported.get("revision") == project["revision"]
                and portable.get("schema") == "motionwright.native.alab-source-portability/1"
                and portable.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
                and portable.get("adoption_receipt_sha256") == adoption_sha
                and portable.get("source_project") == project
                and portable.get("source_generation") == project["generation"]
                and portable.get("imported_generation") == imported["generation"]
                and portable.get("source_assets") == source_assets
                and portable.get("originals") == descriptor["originals"]
                and type(portable.get("production_receipt_count")) is int
                and portable["production_receipt_count"] == 0
                and all(portable.get(key) is True for key in (
                    "fresh_generation_observed", "separate_owner_cas", "project_reopened_equal",
                    "all_source_blobs_reopened_equal", "whole_original_otio_retained",
                    "source_fidelity_unavailable"))
                and all(portable.get(key) is False for key in (
                    "local_production_receipts_inherited", "execution_authority_issued",
                    "native_editor_dispatched_here", "automatic_retry",
                    "human_acceptance", "release_authorized"))
                and media_bridge.HEX.fullmatch(str(portable.get("inspection_fingerprint", ""))) is not None,
                "alab_portable_source_import_did_not_keep_originals_and_separate_generation")
        manifest_path = bundle / "manifest.json"
        manifest_raw = read_closed_bytes(manifest_path, portable.get("manifest_sha256"),
                                         2 * 1024 * 1024)
        pinned_outputs[manifest_path] = (portable["manifest_sha256"], manifest_raw)
        portable_reopened = call("alab-source-portable-readback", (restored_database, bundle_receipt),
                                 "alab-source-portable-readback-cli")
        require(portable_reopened.get("schema") == "motionwright.native.alab-source-portable-readback/1"
                and portable_reopened.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
                and portable_reopened.get("portability_receipt_sha256") == portability_sha
                and portable_reopened.get("project_id") == project["id"]
                and portable_reopened.get("generation") == imported["generation"]
                and portable_reopened.get("revision") == imported["revision"]
                and portable_reopened.get("project_value_sha256") == portable.get("imported_project_value_sha256")
                and portable_reopened.get("source_assets") == source_assets
                and all(portable_reopened.get(key) is True for key in (
                    "project_reopened_equal", "all_source_blobs_reopened_equal",
                    "source_fidelity_unavailable", "fresh_generation_observed"))
                and all(portable_reopened.get(key) is False for key in (
                    "canonical_project_mutation_performed", "execution_authority_issued",
                    "native_editor_dispatched_here", "local_production_receipts_inherited",
                    "human_acceptance", "release_authorized")),
                "alab_portable_source_new_process_readback_did_not_bind_the_imported_owner")
        fixture._new_original(portable_readback_path,
            (json.dumps(portable_reopened, sort_keys=True, indent=2) + "\n").encode())
        _, portable_readback_sha = remember(portable_readback_path, portable_reopened)
        unchanged()
        for name, content in (("project.json", project), ("original.otio", adopted.get("canonical_otio"))):
            require(type(content) is dict, "alab_missing_canonical_service_export")
            fixture._new_original(root / name,
                (json.dumps(content, sort_keys=True, indent=2) + "\n").encode())
        bindings = []
        for index, entry in enumerate(source_assets["media"]):
            require(type(entry) is dict and entry.get("sha256") == original_hashes["media" + str(index)]["sha256"]
                    and entry.get("bytes") == original_hashes["media" + str(index)]["bytes"]
                    and entry.get("media_type") == "video/quicktime"
                    and entry.get("name") == names[4 + index],
                    "alab_returned_media_asset_is_not_its_original_MOV")
            bindings.append({"scene_id": entry.get("scene_id"), "asset_id": entry.get("asset_id"),
                "file": copied["media" + str(index)].name, "sha256": entry["sha256"],
                "source_start_frame": 0, "rights": copy.deepcopy(media_rights[index])})
        request = {"schema": media_bridge.SCHEMA, "project": "project.json", "otio": "original.otio",
                   "bindings": bindings, "owner_review": copy.deepcopy(owner_review)}
        fixture._new_original(root / "alab-portable-request.original.json",
            (json.dumps(request, sort_keys=True, indent=2) + "\n").encode())
        exported = media_bridge.export_portable(root, request, destination, retained_alab=retained,
                                                 source_notices=notice_raw)
        require(exported.get("verified_media_refs") == 2 and exported.get("missing_refs") == 0
                and exported.get("owner_publication_authorized") is False,
                "alab_export_did_not_preserve_the_selected_two_bound_originals")
        preview = media_bridge.import_preview(destination, "timeline.otio",
                                             destination / "source-project.json", retained_alab=retained)
        require(preview.get("native_change_proposals") == []
                and preview.get("media_refs_verified") == 2
                and preview.get("edit_imported") is False,
                "alab_unedited_canonical_export_does_not_read_back_without_a_proposal")
        unchanged()
        # The same bridge exporter includes these exact notices in both its
        # directory and its standalone ZIP. Revalidate the retained companions;
        # this caller does not rewrite or append to the original bridge archive.
        for role, name, size, digest in _ALAB_SOURCE_NOTICES:
            require(read_closed_bytes(destination / name, digest, size) == notice_raw[role],
                    "alab_portable_notice_copy_changed:" + role)
        fixture._new_original(destination / "ALab-HANDOFF-SCOPE.json", (json.dumps({
            "schema": "motionwright.alab-private-selected-handoff/1",
            "original_notices": notice_records,
            "portable_zip_sha256": exported["portable_zip_sha256"],
            "retain_all_original_notices_when_redistributing": True,
            "portable_zip_includes_all_original_notices": True,
            "source_changes": "none; the two MOV and whole OTIO remain unmodified; separate source selection, custody and canonical cut metadata added",
            "third_party_audio_selected": False, "music_or_voice_clearance_claimed": False,
            "endorsement_claimed": False, "human_acceptance": False, "release_authorized": False,
        }, sort_keys=True, indent=2) + "\n").encode())
        result = {
            "schema": "motionwright.native.alab-complete-source-G2-input/1",
            "profile": media_bridge.ALAB_RETAINED_PROFILE,
            "canonical_source_adopted": True, "source_adoption_history_events": 7,
            "sqlite_source_reopened_equal": True, "all_source_blobs_reopened_equal": True,
            "whole_original_otio_retained": True, "source_fidelity_unavailable": True,
            "new_process_source_readback": True, "new_process_portable_readback": True,
            "separate_owner_cas": True, "fresh_generation_observed": True,
            "source_generation": project["generation"], "portable_generation": imported["generation"],
            "canonical_source_stamp": {"project_id": project["id"], "generation": project["generation"],
                                       "revision": project["revision"]},
            "originals": original_hashes, "original_notices": notice_records,
            "derived_descriptor_sha256": hashlib.sha256(derived_bytes).hexdigest(),
            "owner_example_sha256": binary_sha,
            "source_call_originals": _owner_call_original_bindings(call_originals, root),
            "adoption_receipt_sha256": adoption_sha, "source_readback_sha256": reopen_sha,
            "bundle_receipt_sha256": portability_sha, "portable_readback_sha256": portable_readback_sha,
            "bundle_manifest_sha256": portable["manifest_sha256"],
            "bundle_inspection_fingerprint": portable["inspection_fingerprint"],
            "original_portable_manifest_sha256": fixture.sha(destination / "bridge-manifest.json"),
            "source_frames": 234, "source_frame_rate": 24, "source_scene_frames": [52, 182],
            "expected_later_edited_scene_frames": [52, 170],
            "source_origin_is_new_retained_original_derivation": True,
            "remaining_source_references": "opaque/unavailable for this selected two-occurrence profile",
            "historical_readback_rewritten": False, "new_decode": False, "new_render": False,
            "edit_imported": False, "nle_opened": False, "nle_receiver_acceptance": "NOT_VERIFIED",
            "per_scene_native_editability_verified": False, "local_production_receipts_inherited": False,
            "execution_authority_issued": False, "automatic_retry": False,
            "human_acceptance": False, "release_authorized": False,
        }
        fixture._new_original(destination / "alab-complete-native-G2-input.json",
            (json.dumps(result, sort_keys=True, indent=2) + "\n").encode())
        return result, retained


def derive_alab_owner_preview(baseline: dict, returned: dict, source: dict,
                              source_sha: str) -> dict:
    require = fixture.require
    scenes = source.get("scenes", [])
    require(type(scenes) is list and len(scenes) == 2
            and [scene.get("name") for scene in scenes] == ["mk020_0010", "mk020_0020"]
            and [scene.get("duration") for scene in scenes] == [
                {"num": "13", "den": "6"}, {"num": "91", "den": "12"}],
            "alab_owner_requires_original_52_and182_frame_canonical_scenes")
    ids = [scene.get("id") for scene in scenes]
    require(len(set(ids)) == 2
            and baseline.get("schema") == "motionwright.otio-edit-preview/1"
            and returned.get("schema") == "motionwright.kdenlive-bound-cut-preview/1"
            and returned.get("profile") == "kdenlive-bound-video-cut-v1"
            and returned.get("status") == "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
            "alab_owner_requires_the_existing_source_bound_preview_contracts")
    for key, value in (
        ("source_project_sha256", source_sha), ("project_id", source.get("id")),
        ("generation", source.get("generation")), ("expected_project_revision", source.get("revision")),
        ("original_scene_order", ids), ("proposed_scene_order", ids), ("media_refs_verified", 2),
        ("edit_imported", False), ("source_media_relinked", False),
        ("owner_revision_commit_required", True), ("human_review_required", True),
        ("semantic_native_roundtrip", "NOT_SUPPORTED"), ("nle_receiver_acceptance", "NOT_VERIFIED"),
        ("publication_approved", False),
    ):
        require(key in baseline and key in returned
                and type(baseline[key]) is type(value) and type(returned[key]) is type(value)
                and baseline[key] == value and returned[key] == value,
                "alab_owner_preview_source_or_authority_changed:" + key)
    expected = [{"type": "set_scene_duration", "scene_id": ids[1],
                 "duration": {"num": "85", "den": "12"}}]
    require(baseline.get("native_change_proposals") == []
            and baseline.get("missing_references_retained") == 0
            and baseline.get("rendering_performed") is False
            and returned.get("native_change_proposals") == expected
            and returned.get("original_missing_scene_ids") == []
            and returned.get("raw_files_rewritten") is False
            and returned.get("native_final_frames_verified") is False
            and returned.get("native_project_reopened") == "NOT_VERIFIED"
            and type(returned.get("received_clip_count")) is int and returned["received_clip_count"] == 2
            and type(returned.get("edited_clip_count")) is int and returned["edited_clip_count"] == 2,
            "alab_owner_requires_only_second182to170_and_preserves_all_unobserved_flags")
    require(baseline.get("property_fidelity") == returned.get("property_fidelity")
            and len(baseline.get("property_fidelity", [])) == 12
            and type(baseline.get("loss_report")) is list and baseline["loss_report"]
            and type(returned.get("loss_report")) is list
            and returned["loss_report"][:len(baseline["loss_report"])] == baseline["loss_report"],
            "alab_owner_cannot_drop_original_property_losses")
    derived = copy.deepcopy(baseline)
    derived["native_change_proposals"] = expected
    derived["loss_report"] = copy.deepcopy(returned["loss_report"])
    return derived


def finish_alab_return_once(capsule: Path, evidence: Path, database: Path,
                            native_path: Path, native_sha: str, returned_preview: Path,
                            source_input: dict, retained_alab) -> dict:
    """Commit the admitted returned cut and source, then reopen the existing bundle.

    Each Service effect has its own request/intent/original outcome in the
    native example. The first call may commit the cut before a later source
    import fails; its UNKNOWN/failed namespace is never resumed here.
    """
    import hashlib
    import bridge as media_bridge
    from kdenlive_readback import read_closed_bytes
    require = fixture.require
    require(type(source_input) is dict
            and source_input.get("schema") == "motionwright.native.alab-complete-source-G2-input/1"
            and source_input.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and source_input.get("canonical_source_adopted") is True
            and source_input.get("new_process_source_readback") is True
            and source_input.get("new_process_portable_readback") is True
            and source_input.get("source_fidelity_unavailable") is True
            and source_input.get("execution_authority_issued") is False
            and source_input.get("human_acceptance") is False
            and source_input.get("release_authorized") is False,
            "alab_return_requires_the_observed_original_canonical_adoption")
    adoption_path = evidence / "alab-source/alab-adoption-receipt.json"
    adoption_bytes = read_closed_bytes(adoption_path, source_input.get("adoption_receipt_sha256"), 2 * 1024 * 1024)
    adoption = media_bridge.strict_json(adoption_bytes, "original ALab adoption")
    source_path = capsule / "source-project.json"
    source_bytes, source_observed = capture_original(source_path, 2 * 1024 * 1024)
    source_digest = source_observed["sha256"]
    source = media_bridge.strict_json(source_bytes, "canonical ALab source")
    require(source == adoption.get("project") and type(source.get("revision")) is int
            and native_path == evidence / "edited.kdenlive"
            and returned_preview == evidence / "reopened-edited-cut-preview.json"
            and database == evidence / "canonical-source.sqlite3"
            and database.is_file() and not database.is_symlink()
            and evidence.is_absolute() and evidence.resolve(strict=True) == evidence,
            "alab_return_original_paths_or_source_project_changed")
    native_bytes = read_closed_bytes(native_path, native_sha, 5 * 1024 * 1024)
    raw_bytes, raw_observed = capture_original(returned_preview, 2 * 1024 * 1024)
    raw_sha = raw_observed["sha256"]
    returned = media_bridge.strict_json(raw_bytes, "original ALab returned preview")
    baseline = media_bridge.import_preview(capsule, "timeline.otio", source_path,
                                           retained_alab=retained_alab)
    derived = derive_alab_owner_preview(baseline, returned, source, source_digest)
    binary_sha = source_input.get("owner_example_sha256")
    require(type(binary_sha) is str and media_bridge.HEX.fullmatch(binary_sha)
            and fixture.sha(OWNER_EXAMPLE) == binary_sha,
            "alab_return_example_no_longer_matches_the_original_source_producer")
    preview_path = evidence / "alab-owner-cut-preview.json"
    receipt_path = evidence / "alab-returned-source-receipt.json"
    readback_path = evidence / "alab-returned-source-reopen.json"
    bundle = evidence / "alab-edited-source-portable"
    destination_owner = evidence / "alab-edited-restored-owner"
    destination = destination_owner / "motionwright.sqlite3"
    bundle_receipt = evidence / "alab-edited-bundle-receipt.json"
    portable_readback_path = evidence / "alab-edited-portable-reopen.json"
    derived_bytes = (json.dumps(derived, sort_keys=True, indent=2) + "\n").encode()
    fixture._new_original(preview_path, derived_bytes)
    pinned = {
        adoption_path: (source_input["adoption_receipt_sha256"], adoption_bytes),
        source_path: (source_digest, source_bytes), native_path: (native_sha, native_bytes),
        returned_preview: (raw_sha, raw_bytes), preview_path: (hashlib.sha256(derived_bytes).hexdigest(), derived_bytes),
    }
    fixture._new_original(evidence / "alab-returned-source-input.original.json", (json.dumps({
        "schema": "motionwright.native.alab-returned-source-input/1",
        "profile": media_bridge.ALAB_RETAINED_PROFILE,
        "adoption_receipt_sha256": source_input["adoption_receipt_sha256"],
        "source_project_sha256": source_digest, "original_Kdenlive_preview_sha256": raw_sha,
        "owner_preview_sha256": hashlib.sha256(derived_bytes).hexdigest(),
        "native_source_sha256": native_sha, "native_source_bytes": len(native_bytes),
        "owner_example_sha256": binary_sha, "two_separate_revision_operations": True,
        "automatic_retry": False, "effects_absent_claimed": False,
        "human_acceptance": False, "release_authorized": False,
    }, sort_keys=True, indent=2) + "\n").encode())

    call_originals = {}

    def unchanged():
        _owner_call_originals_unchanged(call_originals)
        require(fixture.sha(OWNER_EXAMPLE) == binary_sha, "alab_return_example_changed_between_calls")
        for path, (digest, raw) in pinned.items():
            limit = 5 * 1024 * 1024 if path == native_path else 2 * 1024 * 1024
            require(read_closed_bytes(path, digest, limit) == raw, "alab_return_retained_original_changed")

    def observe(path, expected=None):
        raw, observed = capture_original(path, 2 * 1024 * 1024)
        digest = observed["sha256"]
        value = media_bridge.strict_json(raw, "original ALab return receipt")
        require(expected is None or value == expected, "alab_return_stdout_differs_from_original_receipt")
        pinned[path] = (digest, raw)
        return value, digest

    def call(mode, args, prefix):
        unchanged()
        argv = [str(OWNER_EXAMPLE), mode, *(str(value) for value in args)]
        output = fixture.run_firstparty_native(argv, timeout=90, evidence_prefix=evidence / prefix)
        _freeze_owner_call_originals(evidence / prefix, argv, output, call_originals)
        return media_bridge.strict_json(output, "original ALab return native output")

    result = call("alab-returned-cut", (database, adoption_path, source_path, preview_path,
                  returned_preview, native_path, native_sha, receipt_path), "alab-returned-cut-cli")
    _, receipt_sha = observe(receipt_path, result)
    final = result.get("committed_project")
    require(type(final) is dict and final.get("id") == source.get("id")
            and final.get("generation") == source.get("generation")
            and type(final.get("revision")) is int and final["revision"] == source["revision"] + 2
            and result.get("schema") == "motionwright.native.alab-returned-cut-and-source/1"
            and result.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and result.get("adoption_receipt_sha256") == source_input["adoption_receipt_sha256"]
            and result.get("source_project_sha256") == source_digest
            and result.get("owner_preview_sha256") == hashlib.sha256(derived_bytes).hexdigest()
            and result.get("original_Kdenlive_preview_sha256") == raw_sha
            and result.get("cut_request_id") == "original:alab-kdenlive-cut:" + raw_sha
            and result.get("source_import_request_id") == "original:alab-kdenlive-capsule:" + native_sha
            and result.get("native_source") == {"sha256": native_sha, "size_bytes": len(native_bytes)}
            and type(result.get("exact_additional_history_events")) is int
            and result["exact_additional_history_events"] == 2
            and all(result.get(key) is True for key in (
                "owner_cut_single_revision_committed", "native_source_additional_revision_committed",
                "whole_original_otio_retained", "all_four_source_blobs_reopened_equal",
                "source_fidelity_unavailable", "sqlite_source_reopened_equal"))
            and all(result.get(key) is False for key in (
                "native_editor_dispatched_here", "per_scene_native_editability_verified",
                "execution_authority_issued", "automatic_retry", "human_acceptance", "release_authorized")),
            "alab_return_original_commit_and_source_outcomes_are_not_bound")
    stages = result.get("stage_receipts")
    require(type(stages) is list and len(stages) == 2, "alab_return_missing_two_original_outcomes")
    for index, stage in enumerate(stages):
        number = index + 7
        path = receipt_path.with_suffix(f".stage-{number:02}.result.original.json")
        require(type(stage) is dict and type(stage.get("index")) is int and stage["index"] == number
                and stage.get("file") == path.name,
                "alab_return_original_outcome_member_not_closed")
        raw = read_closed_bytes(path, stage.get("sha256"), 2 * 1024 * 1024)
        require(type(stage.get("bytes")) is int and stage["bytes"] == len(raw),
                "alab_return_original_outcome_size_changed")
        pinned[path] = (stage["sha256"], raw)
    reopened = call("alab-returned-readback", (database, adoption_path, receipt_path, receipt_sha),
                    "alab-returned-readback-cli")
    require(reopened.get("schema") == "motionwright.native.alab-returned-source-readback/1"
            and reopened.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and reopened.get("returned_source_receipt_sha256") == receipt_sha
            and reopened.get("project_id") == final["id"]
            and reopened.get("generation") == final["generation"] and reopened.get("revision") == final["revision"]
            and reopened.get("project_value_sha256") == result.get("committed_project_value_sha256")
            and reopened.get("source_assets") == result.get("source_assets")
            and reopened.get("native_source") == result.get("native_source")
            and all(reopened.get(key) is True for key in (
                "project_reopened_equal", "exact_nine_history_events", "all_four_source_blobs_reopened_equal",
                "source_fidelity_unavailable"))
            and all(reopened.get(key) is False for key in (
                "canonical_project_mutation_performed", "native_editor_dispatched_here",
                "execution_authority_issued", "human_acceptance", "release_authorized")),
            "alab_return_independent_readback_does_not_match_original_nine_event_source")
    fixture._new_original(readback_path, (json.dumps(reopened, sort_keys=True, indent=2) + "\n").encode())
    _, reopen_sha = observe(readback_path, reopened)
    fixture._new_original(evidence / "alab-edited-restored-owner.intent.json", (json.dumps({
        "state": "INTENT", "destination": str(destination_owner), "returned_source_receipt_sha256": receipt_sha,
        "automatic_retry": False}, sort_keys=True) + "\n").encode())
    destination_owner.mkdir(mode=0o700)
    portable = call("alab-edited-bundle", (database, adoption_path, receipt_path, receipt_sha, bundle,
                    destination, bundle_receipt), "alab-edited-bundle-cli")
    _, portable_sha = observe(bundle_receipt, portable)
    imported = portable.get("imported_project")
    require(type(imported) is dict and imported.get("id") == final["id"]
            and type(imported.get("generation")) is str and media_bridge.UUID.fullmatch(imported["generation"])
            and imported["generation"] != final["generation"] and imported.get("revision") == final["revision"]
            and portable.get("schema") == "motionwright.native.alab-edited-source-portability/1"
            and portable.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and portable.get("returned_source_receipt_sha256") == receipt_sha
            and portable.get("source_project") == final
            and portable.get("source_generation") == final["generation"]
            and portable.get("imported_generation") == imported["generation"]
            and portable.get("source_assets") == result.get("source_assets")
            and portable.get("native_source") == result.get("native_source")
            and type(portable.get("production_receipt_count")) is int and portable["production_receipt_count"] == 0
            and type(portable.get("inspection_fingerprint")) is str
            and media_bridge.HEX.fullmatch(portable["inspection_fingerprint"])
            and all(portable.get(key) is True for key in (
                "fresh_generation_observed", "separate_owner_cas", "project_reopened_equal",
                "all_four_source_blobs_reopened_equal", "source_fidelity_unavailable", "whole_original_otio_retained"))
            and all(portable.get(key) is False for key in (
                "local_production_receipts_inherited", "execution_authority_issued", "native_editor_dispatched_here",
                "per_scene_native_editability_verified", "automatic_retry", "human_acceptance", "release_authorized")),
            "alab_edited_portable_source_changed_authored_scope_or_inherited_authority")
    manifest = bundle / "manifest.json"
    manifest_raw = read_closed_bytes(manifest, portable.get("manifest_sha256"), 2 * 1024 * 1024)
    pinned[manifest] = (portable["manifest_sha256"], manifest_raw)
    final_readback = call("alab-edited-portable-readback", (destination, bundle_receipt, portable_sha),
                         "alab-edited-portable-readback-cli")
    require(final_readback.get("schema") == "motionwright.native.alab-edited-portable-readback/1"
            and final_readback.get("profile") == media_bridge.ALAB_RETAINED_PROFILE
            and final_readback.get("bundle_receipt_sha256") == portable_sha
            and final_readback.get("project_id") == final["id"]
            and final_readback.get("generation") == imported["generation"]
            and final_readback.get("revision") == imported["revision"]
            and final_readback.get("project_value_sha256") == portable.get("imported_project_value_sha256")
            and final_readback.get("source_assets") == result.get("source_assets")
            and final_readback.get("native_source") == result.get("native_source")
            and all(final_readback.get(key) is True for key in (
                "project_reopened_equal", "all_four_source_blobs_reopened_equal", "source_fidelity_unavailable",
                "fresh_generation_observed", "whole_original_otio_retained"))
            and all(final_readback.get(key) is False for key in (
                "canonical_project_mutation_performed", "local_production_receipts_inherited",
                "execution_authority_issued", "native_editor_dispatched_here", "human_acceptance", "release_authorized")),
            "alab_edited_portable_new_process_reopen_did_not_match_the_original_source")
    fixture._new_original(portable_readback_path,
        (json.dumps(final_readback, sort_keys=True, indent=2) + "\n").encode())
    _, portable_reopen_sha = observe(portable_readback_path, final_readback)
    unchanged()
    return {
        "schema": "motionwright.native.alab-returned-source-completion/1", "profile": media_bridge.ALAB_RETAINED_PROFILE,
        "owner_cut_single_revision_committed": True, "native_source_additional_revision_committed": True,
        "additional_history_events": 2, "total_original_history_events": 9,
        "new_process_source_reopened_equal": True, "new_process_portable_reopened_equal": True,
        "all_four_source_blobs_reopened_equal": True, "whole_original_otio_retained": True,
        "source_fidelity_unavailable": True, "native_source_sha256": native_sha,
        "native_source_bytes": len(native_bytes), "source_generation": final["generation"],
        "portable_generation": imported["generation"], "original_second_frames": 182, "edited_second_frames": 170,
        "returned_source_receipt_sha256": receipt_sha, "returned_source_readback_sha256": reopen_sha,
        "edited_bundle_receipt_sha256": portable_sha, "edited_portable_readback_sha256": portable_reopen_sha,
        "bundle_manifest_sha256": portable["manifest_sha256"],
        "bundle_inspection_fingerprint": portable["inspection_fingerprint"],
        "stage_originals": copy.deepcopy(stages),
        "source_call_originals": _owner_call_original_bindings(call_originals, evidence),
        "local_production_receipts_inherited": False, "execution_authority_issued": False,
        "per_scene_native_editability_verified": False, "native_editor_dispatched_here": False,
        "automatic_retry": False, "human_acceptance": False, "release_authorized": False,
    }
