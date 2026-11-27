"""Check same-job custody before any macOS cold reopen."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import kdenlive_macos_reopen as reopen
from kdenlive_native_distribution import publish


def bytes_identity(path):
    original = path.read_bytes()
    return {"bytes": len(original), "sha256": sha256(original).hexdigest()}


class MacColdReopenTests(unittest.TestCase):
    def original(self, owner):
        source = {"path": str(owner / "otio-original" / "timeline.otio"),
                  "bytes": 11, "sha256": "b" * 64}
        context = {"source_sha": "a" * 40, "runner_os": "macOS", "run_id": "1", "run_attempt": "1"}
        editor = owner / "kdenlive"
        binary = {"bytes": 120, "sha256": "c" * 64}
        initial_dir = owner / "macos-initial"
        initial_dir.mkdir()
        initial = initial_dir / "initial.kdenlive"
        initial.write_bytes(b"original project")
        saved = {"path": str(initial), **bytes_identity(initial)}
        media_bytes = b"original h264"
        media_sha = sha256(media_bytes).hexdigest()
        media = owner / "otio-original" / "media" / (media_sha + ".mp4")
        media.parent.mkdir(parents=True)
        media.write_bytes(media_bytes)
        publish(initial_dir / "initial-project.json", {
            "schema": "motionwright.kdenlive-macos-initial-project/1",
            "status": "INITIAL_PROJECT_VERIFIED", "context": context, "pid": 12,
            "editor": {"path": str(editor), **binary}, "source": source, "project": saved,
            "import_confirmations": 1, "save_confirmations": 1, "render_dispatches": 0,
        })
        publish(initial_dir / "project-saved.json", {"pid": 12, **saved})
        publish(initial_dir / "editor-terminal.json", {"pid": 12, "exit_code": 0})
        publish(initial_dir / "initial-readback.json", {
            "native_file_sha256": saved["sha256"],
            "exact_original_source_SHA_set_conserved": True,
            "observed_source_media": [{
                "authorized_bundle_relative": "media/" + media.name,
                "original_media_sha256": media_sha,
                "original_media_bytes": len(media_bytes),
            }],
        })
        publish(owner / "otio-source-ready.json", {"context": context, "timeline": source})
        return context, editor, binary, initial, media

    def test_same_input_is_resolved_from_verified_save_and_closed_process(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = Path(directory).resolve()
            context, editor, binary, initial, media = self.original(owner)
            with patch.object(reopen, "installed_editor", return_value=(editor, binary)):
                admitted = reopen.admit_original(owner, context, "distribution")
            self.assertEqual(admitted.project, initial)
            self.assertEqual(admitted.media, media)
            self.assertEqual(admitted.first_pid, 12)

    def test_changed_xml_or_media_is_refused_before_a_new_editor_can_launch(self):
        for change in ("project", "media"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                owner = Path(directory).resolve()
                context, editor, binary, initial, media = self.original(owner)
                (initial if change == "project" else media).write_bytes(b"replacement")
                with patch.object(reopen, "installed_editor", return_value=(editor, binary)), patch.object(
                    reopen.subprocess, "Popen",
                ) as launch:
                    with self.assertRaisesRegex(ValueError, "original"):
                        reopen.run(owner, context, "distribution")
                launch.assert_not_called()

    def test_native_return_exports_the_unchanged_cut_then_saves_under_a_new_name(self):
        ui, evidence, check = Mock(), Mock(), Mock()
        output = Path("macos-reopen")
        with patch.object(reopen, "export_otio") as export, patch.object(
            reopen, "select_normal_tool",
        ), patch.object(reopen, "expect_third_clip") as expect, patch.object(
            reopen, "save_project_as",
        ) as save, patch.object(reopen, "wait_project"):
            reopen.produce_native_return(ui, evidence, output, check)

        self.assertEqual(export.call_args_list, [
            call(ui, evidence, output / "received.otio", "original-export", check),
            call(ui, evidence, output / "edited.otio", "edited-export", check),
        ])
        self.assertEqual(expect.call_args_list, [
            call(ui, evidence, "edit-return", 30), call(ui, evidence, "verify-return", 29),
        ])
        ui.resize_selected_end.assert_called_once_with("edit-return", 0, evidence)
        save.assert_called_once_with(ui, evidence, output / "edited.kdenlive", check, "edited-save")

    def test_failed_trim_readback_keeps_the_original_export_and_stops_before_edited_save(self):
        ui, evidence, check = Mock(), Mock(), Mock()
        output = Path("macos-reopen")
        with patch.object(reopen, "export_otio") as export, patch.object(
            reopen, "select_normal_tool",
        ), patch.object(reopen, "expect_third_clip", side_effect=[None, ValueError("wrong duration")]), patch.object(
            reopen, "save_project_as",
        ) as save:
            with self.assertRaisesRegex(ValueError, "wrong duration"):
                reopen.produce_native_return(ui, evidence, output, check)
        export.assert_called_once_with(ui, evidence, output / "received.otio", "original-export", check)
        ui.resize_selected_end.assert_called_once_with("edit-return", 0, evidence)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
