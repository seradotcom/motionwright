"""Closed source-to-owner conversion and one-shot failure boundaries; no native effects."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import kdenlive_owner_cut as owner


def originals():
    ids = ["scene-a", "scene-b", "scene-c"]
    source = {"id": "project", "generation": "generation", "revision": 7,
              "scenes": [{"id": ident, "duration": {"num": "1", "den": "1"}}
                         for ident in ids]}
    baseline = {
        "schema": "motionwright.otio-edit-preview/1",
        "source_project_sha256": "a" * 64, "project_id": "project",
        "generation": "generation", "expected_project_revision": 7,
        "original_scene_order": ids, "proposed_scene_order": ids,
        "native_change_proposals": [], "media_refs_verified": 3,
        "missing_references_retained": 0, "loss_report": ["Preserve source-only loss"],
        "property_fidelity": [{"property": str(i)} for i in range(12)],
        "edit_imported": False, "source_media_relinked": False,
        "owner_revision_commit_required": True, "human_review_required": True,
        "rendering_performed": False, "semantic_native_roundtrip": "NOT_SUPPORTED",
        "nle_receiver_acceptance": "NOT_VERIFIED", "publication_approved": False,
    }
    returned = copy.deepcopy(baseline)
    for field in ("rendering_performed", "missing_references_retained"):
        returned.pop(field)
    returned.update(
        schema="motionwright.kdenlive-bound-cut-preview/1",
        profile="kdenlive-bound-video-cut-v1",
        status="BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
        original_missing_scene_ids=[], raw_files_rewritten=False,
        native_final_frames_verified=False, native_project_reopened="NOT_VERIFIED",
        received_clip_count=3, edited_clip_count=3,
        native_change_proposals=[{"type": "set_scene_duration", "scene_id": ids[2],
                                  "duration": {"num": "1", "den": "2"}}],
        loss_report=baseline["loss_report"] + ["Retain native-identity loss"],
    )
    return baseline, returned, source


class KdenliveOwnerCutContracts(unittest.TestCase):
    def test_existing_flags_and_every_loss_survive_in_separate_derived_preview(self):
        baseline, returned, source = originals()
        before = copy.deepcopy((baseline, returned, source))
        derived = owner.derive_owner_preview(baseline, returned, source, "a" * 64)
        self.assertEqual((baseline, returned, source), before)
        self.assertEqual(derived["schema"], baseline["schema"])
        self.assertEqual(derived["loss_report"], returned["loss_report"])
        self.assertEqual(derived["property_fidelity"], baseline["property_fidelity"])
        self.assertTrue(derived["human_review_required"])
        self.assertFalse(derived["edit_imported"])
        self.assertFalse(derived["rendering_performed"])
        derived["loss_report"].append("Only the new value changes")
        self.assertEqual((baseline, returned, source), before)

    def test_stale_or_mixed_project_generation_revision_or_digest_rejects(self):
        for field, wrong in (("project_id", "foreign"), ("generation", "foreign"),
                             ("expected_project_revision", 8),
                             ("expected_project_revision", True),
                             ("source_project_sha256", "b" * 64)):
            with self.subTest(field=field, wrong=wrong):
                baseline, returned, source = originals()
                returned[field] = wrong
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_imported_or_human_or_receiver_authority_cannot_be_promoted(self):
        for field, wrong in (("edit_imported", True), ("publication_approved", True),
                             ("human_review_required", False),
                             ("owner_revision_commit_required", False),
                             ("native_project_reopened", "VERIFIED"),
                             ("native_final_frames_verified", True),
                             ("nle_receiver_acceptance", "VERIFIED"),
                             ("raw_files_rewritten", True),
                             ("status", "REJECTED_LOSS")):
            with self.subTest(field=field):
                baseline, returned, source = originals()
                returned[field] = wrong
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_only_exact_returned_third_scene_half_second_end_trim_is_admitted(self):
        for changes in ([], [{"type": "move_scene", "scene_id": "scene-c", "to_index": 0}],
                        [{"type": "set_scene_duration", "scene_id": "scene-a",
                          "duration": {"num": "1", "den": "2"}}],
                        [{"type": "set_scene_duration", "scene_id": "scene-c",
                          "duration": {"num": "2", "den": "1"}}]):
            with self.subTest(changes=changes):
                baseline, returned, source = originals()
                returned["native_change_proposals"] = changes
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_dropped_property_or_loss_cannot_be_hidden_in_legacy_conversion(self):
        for field, value in (("loss_report", ["replacement"]),
                             ("property_fidelity", [{"property": "new"}]),
                             ("original_missing_scene_ids", ["scene-c"]),
                             ("media_refs_verified", 2)):
            with self.subTest(field=field):
                baseline, returned, source = originals()
                returned[field] = value
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def test_non_original_duration_or_baseline_edit_is_rejected(self):
        for mutate in (lambda b, r, s: s["scenes"][2].update(duration={"num": "2", "den": "1"}),
                       lambda b, r, s: b.update(native_change_proposals=r["native_change_proposals"]),
                       lambda b, r, s: b.update(missing_references_retained=1)):
            with self.subTest(mutate=mutate):
                baseline, returned, source = originals()
                mutate(baseline, returned, source)
                with self.assertRaises(owner.fixture.KdenliveCompleteFixtureRefused):
                    owner.derive_owner_preview(baseline, returned, source, "a" * 64)

    def one_shot(self, outcomes):
        baseline, returned, source = originals()
        evidence = Path("/isolated/evidence")
        receipt = {"schema": "motionwright.native.otio-owner-cut-commit/1",
                   "single_revision_committed": True, "human_acceptance": False}
        def original_bytes(path):
            if path.name == "source-project.json":
                return json.dumps(source).encode()
            if path.name == "owner-cut-commit-receipt.json":
                return json.dumps(receipt).encode()
            return json.dumps(returned).encode()
        with patch.object(owner, "import_preview", return_value=baseline), \
             patch.object(Path, "read_bytes", original_bytes), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "is_symlink", return_value=False), \
             patch.object(Path, "resolve", lambda path, **kwargs: path), \
             patch.object(owner.fixture, "sha", return_value="a" * 64), \
             patch.object(owner.fixture, "_new_original"), \
             patch.object(owner.fixture, "run_firstparty_native",
                          side_effect=outcomes) as run:
            try:
                owner.commit_once(Path("/isolated/capsule"), evidence,
                                  evidence / "source.sqlite3",
                                  evidence / "reopened-edited-cut-preview.json")
            except BaseException as error:
                return run, error
        self.fail("The intentionally incomplete operation was promoted to success")

    def test_unknown_commit_terminal_never_retries_or_launches_readback(self):
        run, error = self.one_shot([TimeoutError("Original commit state unknown")])
        self.assertIsInstance(error, TimeoutError)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1], "commit")

    def test_readback_failure_never_replays_known_commit(self):
        receipt = {"schema": "motionwright.native.otio-owner-cut-commit/1",
                   "single_revision_committed": True, "human_acceptance": False}
        run, error = self.one_shot([json.dumps(receipt).encode(), b'{"project_reopened_equal":false}'])
        self.assertIsInstance(error, owner.fixture.KdenliveCompleteFixtureRefused)
        self.assertEqual([call.args[0][1] for call in run.call_args_list],
                         ["commit", "readback"])


if __name__ == "__main__":
    unittest.main()
