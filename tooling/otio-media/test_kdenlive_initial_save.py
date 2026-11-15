"""Synthetic checks of the original-model barrier before the first Native Save."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import kdenlive_gui_e2e as runner


def original_preview():
    order = ["scene-a", "scene-b", "scene-c"]
    return {"original_scene_order": order, "proposed_scene_order": order[:],
        "status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED",
        "received_clip_count": 3, "edited_clip_count": 3, "media_refs_verified": 3,
        "native_change_proposals": [], "original_missing_scene_ids": [],
        "source_identity_mapping": [{"scene_id": scene, "source_fps": 30,
            "original_inpoint": offset, "duration_frames": 30}
            for scene, offset in zip(order, (0, 30, 60))],
        "nle_receiver_acceptance": "NOT_VERIFIED",
        "native_project_reopened": "NOT_VERIFIED",
        "semantic_native_roundtrip": "NOT_SUPPORTED",
        "native_final_frames_verified": False, "edit_imported": False,
        "raw_files_rewritten": False, "publication_approved": False}


class InitialSaveBarrierTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.first = SimpleNamespace(ui=Mock(), guard=Mock())
        self.records = SimpleNamespace(root=Path("owned"), document=Mock())
        self.first.ui.refresh_third_clip_playlist.side_effect = lambda: self.events.append("refresh")
        self.first.ui.export_otio.side_effect = lambda *a: self.events.append("export")
        self.first.ui.save_as.side_effect = lambda *a: self.events.append("save")
        self.preview = original_preview()

    def run_save(self, preview_error=None, digest_error=None):
        def inspect(*a, **kw):
            self.events.append("model-readback")
            request = a[2]
            self.assertEqual(request["received_otio"], request["edited_otio"])
            self.assertEqual(request["received_sha256"], request["edited_sha256"])
            if preview_error is not None:
                raise preview_error
            return self.preview
        def digest(path, limit):
            if digest_error is not None:
                raise digest_error
            return "b" * 64 if path.name == "initial.kdenlive" else "a" * 64
        with patch.object(runner, "preview_kdenlive_cut", side_effect=inspect), \
             patch.object(runner, "digest", side_effect=digest):
            return runner.save_initial_firstparty_project(
                self.first, Path("capsule"), self.records,
                Path("owned/initial.kdenlive"), Path("owned/received.otio"), "c" * 64)

    def test_original_native_model_is_checked_before_the_one_save(self):
        self.assertEqual(self.run_save(), ("b" * 64, "a" * 64))
        self.assertEqual(self.events, ["refresh", "export", "model-readback", "save"])
        self.first.ui.save_as.assert_called_once()
        value = self.records.document.call_args.args[1]
        self.assertTrue(value["one_original_export_used_for_both_comparison_roles"])
        self.assertFalse(value["saved_project_verified"])
        self.assertFalse(value["native_reopen_verified"])

    def test_uncertain_refresh_stops_before_export_and_save(self):
        error = runner.GuiRejected("synthetic uncertain refresh")
        self.first.ui.refresh_third_clip_playlist.side_effect = error
        with self.assertRaises(runner.GuiRejected) as caught:
            self.run_save()
        self.assertIs(caught.exception, error)
        self.first.ui.export_otio.assert_not_called()
        self.first.ui.save_as.assert_not_called()

    def test_uncertain_export_stops_before_save(self):
        error = runner.GuiRejected("synthetic uncertain export")
        self.first.ui.export_otio.side_effect = error
        with self.assertRaises(runner.GuiRejected) as caught:
            self.run_save()
        self.assertIs(caught.exception, error)
        self.first.ui.save_as.assert_not_called()

    def test_rollback_or_changed_model_never_reaches_save(self):
        changes = [
            lambda v: v["source_identity_mapping"][2].update(duration_frames=29),
            lambda v: v["source_identity_mapping"][2].update(original_inpoint=0),
            lambda v: v["source_identity_mapping"][2].update(source_fps=24),
            lambda v: v["proposed_scene_order"].reverse(),
            lambda v: v.update(status="REJECTED_LOSS"),
            lambda v: v.update(native_change_proposals=[{"type": "set_scene_duration"}]),
            lambda v: v.update(original_missing_scene_ids=["scene-c"]),
            lambda v: v.update(native_project_reopened="VERIFIED"),
        ]
        for change in changes:
            with self.subTest(change=change):
                self.preview = original_preview(); change(self.preview)
                self.first.ui.save_as.reset_mock()
                with self.assertRaises(runner.GuiRejected):
                    self.run_save()
                self.first.ui.save_as.assert_not_called()

    def test_changed_source_readback_stops_before_save(self):
        error = ValueError("synthetic immutable source changed")
        with self.assertRaises(ValueError) as caught:
            self.run_save(preview_error=error)
        self.assertIs(caught.exception, error)
        self.first.ui.save_as.assert_not_called()

    def test_incomplete_export_original_stops_before_save(self):
        error = OSError("synthetic incomplete export custody")
        with self.assertRaises(OSError) as caught:
            self.run_save(digest_error=error)
        self.assertIs(caught.exception, error)
        self.first.ui.save_as.assert_not_called()
