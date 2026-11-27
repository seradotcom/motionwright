"""Test the bounded installed-editor route without macOS UI effects."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import kdenlive_macos_editor as editor


class MacEditorRouteTests(unittest.TestCase):
    def test_one_import_and_save_to_exact_paths(self):
        ui = Mock()
        ui.menu_item.side_effect = ["import item", "save item"]
        ui.file_panel.side_effect = [("input field", "Open"), ("output field", "Save")]
        evidence = Mock()
        original = Path("original") / "timeline.otio"
        initial = Path("evidence") / "initial.kdenlive"

        with patch.object(editor, "refresh_third_playlist") as refresh:
            editor.import_and_save(ui, evidence, original, initial, lambda: None)
        refresh.assert_called_once_with(ui, evidence)

        self.assertEqual(ui.set_text.call_args_list, [
            call("input field", str(original)), call("output field", str(initial)),
        ])
        self.assertEqual(evidence.press.call_args_list, [
            call("open-import", "import item"),
            call("import-otio", "Open"),
            call("open-save-as", "save item"),
            call("save-initial", "Save"),
        ])

    def test_uncertain_import_cannot_trigger_a_second_import_or_a_save(self):
        ui = Mock()
        ui.menu_item.return_value = "import item"
        ui.file_panel.return_value = ("input field", "Open")
        ui.after_import.side_effect = TimeoutError("import uncertain")
        evidence = Mock()

        with self.assertRaisesRegex(TimeoutError, "import uncertain"):
            editor.import_and_save(ui, evidence, Path("timeline.otio"), Path("initial.kdenlive"),
                                   lambda: None)

        self.assertEqual(ui.menu_item.call_count, 1)
        self.assertEqual(evidence.press.call_args_list, [
            call("open-import", "import item"), call("import-otio", "Open"),
        ])

    def test_unverified_input_is_never_confirmed(self):
        ui = Mock()
        ui.menu_item.return_value = "import item"
        ui.file_panel.return_value = ("input field", "Open")
        check = Mock(side_effect=ValueError("changed source"))
        evidence = Mock()

        with self.assertRaisesRegex(ValueError, "changed source"):
            editor.import_and_save(ui, evidence, Path("timeline.otio"), Path("initial.kdenlive"),
                                   check)

        evidence.press.assert_called_once_with("open-import", "import item")
        ui.after_import.assert_not_called()


    def test_open_file_menu_can_use_cocoa_show_menu_but_import_cannot(self):
        with tempfile.TemporaryDirectory() as folder:
            ax = Mock(pid=42)
            ax.enabled.return_value = True
            ax.owns.return_value = True
            ax.text.side_effect = lambda _, name: {"AXRole": "AXMenuItem", "AXTitle": "File"}[name]
            ax.action.side_effect = lambda _, allowed: "AXShowMenu" if "AXShowMenu" in allowed else None
            ax.perform.return_value = 0
            evidence = editor.Evidence(Path(folder), ax, lambda: None)

            evidence.press("import-file-menu", "File")
            self.assertTrue((Path(folder) / "import-file-menu-terminal.json").is_file())

            with self.assertRaisesRegex(ValueError, "no exact action"):
                evidence.press("open-import", "File")
            ax.perform.assert_called_once()
            self.assertFalse((Path(folder) / "open-import-intent.json").exists())

    def test_playlist_model_is_checked_before_each_change_and_after_restore(self):
        ui, evidence = Mock(), Mock()
        ui.duration_dialog.side_effect = [
            ("field", "OK", "Cancel", (60, 60, 30)),
            ("field", "OK", "Cancel", (60, 60, 29)),
            ("field", "OK", "Cancel", (60, 60, 30)),
        ]
        ui.duration_values.side_effect = [((60, 60, 29), "OK"), ((60, 60, 30), "OK")]

        editor.refresh_third_playlist(ui, evidence)

        self.assertEqual(ui.duration_dialog.call_args_list, [
            call("shorten", evidence), call("restore", evidence), call("verify-restoration", evidence),
        ])
        self.assertEqual(ui.commit_timecode.call_args_list, [
            call("field", "00:00:00:29", "shorten", evidence),
            call("field", "00:00:01:00", "restore", evidence),
        ])
        self.assertEqual(evidence.press.call_args_list, [
            call("shorten-accept", "OK"), call("restore-accept", "OK"),
            call("verify-restoration-close", "Cancel"),
        ])

    def test_unknown_or_unapplied_trim_never_restores_or_saves(self):
        ui, evidence = Mock(), Mock()
        ui.duration_dialog.side_effect = [("field", "OK", "Cancel", (60, 60, 30))] * 2
        ui.duration_values.return_value = ((60, 60, 29), "OK")
        with self.assertRaisesRegex(ValueError, "restore model"):
            editor.refresh_third_playlist(ui, evidence)
        evidence.press.assert_called_once_with("shorten-accept", "OK")
        self.assertEqual(ui.commit_timecode.call_count, 1)

    def test_observer_uses_the_exact_installation_without_a_source_capsule(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = Path(directory)
            binary = owner / "kdenlive"
            initial = {"sha256": "a" * 64, "bytes": 123}
            process = Mock(pid=29)
            process.poll.return_value = None
            process.terminate.side_effect = lambda: setattr(process.poll, "return_value", -15)
            context = {"runner_os": "macOS", "run_id": "213", "source_sha": "b" * 40}
            with patch.object(editor, "installed_editor", return_value=(binary, initial)) as install, patch.object(
                editor, "admitted_source", side_effect=AssertionError("observer attempted to open a capsule"),
            ), patch.object(editor, "Accessibility"), patch.object(editor, "EditorUI") as ui_type, patch.object(
                editor, "editor_pid_executable", return_value=binary,
            ), patch.object(editor.subprocess, "Popen", return_value=process):
                ui_type.return_value.diagnostic.return_value = []
                ui_type.return_value.project_monitor_diagnostic.return_value = {"rows": []}
                report = editor.run(owner, context, "distribution", True)

            install.assert_called_once_with(owner, context, "distribution")
            ui_type.return_value.project_monitor_diagnostic.assert_called_once()
            self.assertEqual(report, {"status": "EDITOR_OBSERVED", "pid": 29, "editor_actions": 0})
            self.assertTrue((owner / "macos-initial-observe" / "project-monitor-ax.json").exists())
            self.assertFalse((owner / "otio-source-ready.json").exists())

    def test_visible_clock_needs_no_window_or_toolbar_action(self):
        ui, evidence = Mock(), Mock()
        ax = ui.ax
        ax.text.return_value = "00:00:00:00"
        ui.project_clock.return_value = "clock"
        result = editor.observe_project_clock_layout(ui, evidence)
        self.assertEqual(result["editor_actions"], 0)
        ui.reveal_project_clock.assert_not_called()

    def test_hidden_clock_only_gets_one_scoped_layout_action(self):
        ui, evidence = Mock(), Mock()
        ui.project_clock.return_value = None
        ui.reveal_project_clock.return_value = "resize-owned-window"
        ui.await_target.return_value = "clock"
        ui.ax.text.return_value = "00:00:00:00"

        result = editor.observe_project_clock_layout(ui, evidence)

        ui.reveal_project_clock.assert_called_once_with(evidence)
        self.assertEqual(result["editor_actions"], 1)
        self.assertEqual(result["layout_change"], "resize-owned-window")

    def test_third_clip_selection_uses_native_project_navigation_without_a_clock(self):
        ui = editor.EditorUI(Mock(), lambda: None)
        ui.await_target = Mock(return_value="Project Monitor radio")
        ui.menu_item = Mock(side_effect=["Sequence effects", "Go to Project End", "Rewind 1 Frame", "Select Clip"])
        ui.project_clock = Mock(side_effect=AssertionError("the Project Monitor clock is hidden"))
        ui.note_foreground = Mock()
        evidence = Mock()

        ui.seek_and_select_third_clip(evidence)

        self.assertEqual(ui.menu_item.call_args_list, [
            call("Sequence effects", evidence, "focus-project-monitor", section="Sequence", submenu="Tracks"),
            call("Go to Project End", evidence, "project-end", section="Monitor", submenu="Go To"),
            call("Rewind 1 Frame", evidence, "rewind-one-frame", section="Monitor", submenu="Seek"),
            call("Select Clip", evidence, "select-third", section="Sequence", submenu="Selection"),
        ])
        self.assertEqual(evidence.press.call_args_list, [
            call("activate-project-monitor", "Project Monitor radio"),
            call("focus-project-monitor", "Sequence effects"),
            call("project-end", "Go to Project End"),
            call("rewind-one-frame", "Rewind 1 Frame"),
            call("select-third", "Select Clip"),
        ])
        evidence.key.assert_called_once_with("select-video-track-one", 18)
        self.assertEqual(ui.note_foreground.call_args_list, [
            call(evidence, "before-video-track-one"), call(evidence, "after-video-track-one"),
        ])

    def test_unconfirmed_foreground_stops_before_opening_the_import_menu(self):
        ui, evidence = Mock(), Mock()
        ui.prepare_input.side_effect = TimeoutError("focused window unverifiable")

        with self.assertRaisesRegex(TimeoutError, "focused window unverifiable"):
            editor.import_and_save(ui, evidence, Path("timeline.otio"), Path("initial.kdenlive"), Mock())

        ui.menu_item.assert_not_called()
        evidence.press.assert_not_called()

    def test_command_probe_filters_the_real_action_without_invoking_it(self):
        ui, evidence = Mock(), Mock()
        ui.menu_item.return_value = "Find Action"
        ui.await_target.side_effect = ["command filter", {"role": "AXCell", "title": "Select Video Track 1"}]

        report = editor.observe_video_track_action(ui, evidence)

        evidence.press.assert_called_once_with("open-find-action", "Find Action")
        evidence.key.assert_not_called()
        ui.set_text.assert_called_once_with("command filter", "Select Video Track 1")
        self.assertEqual(report["command"]["title"], "Select Video Track 1")
        self.assertEqual(report["timeline_actions"], 0)

    def test_command_probe_preserves_outline_state_when_the_exact_row_is_unavailable(self):
        ui, evidence = Mock(), Mock()
        ui.await_target.side_effect = ["command filter", TimeoutError("command did not resolve")]
        ui.command_outline.return_value = {"row_counts": {"AXRows": 0}, "nodes": []}

        with self.assertRaisesRegex(TimeoutError, "command did not resolve"):
            editor.observe_video_track_action(ui, evidence)

        evidence.write.assert_any_call("find-action-outline", ui.command_outline.return_value)
        evidence.key.assert_not_called()

if __name__ == "__main__":
    unittest.main()
