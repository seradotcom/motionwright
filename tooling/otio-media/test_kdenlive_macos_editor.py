"""Test the bounded installed-editor route without macOS UI effects."""
import ctypes as C
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

    def test_duration_reads_the_qt_text_range_when_ax_value_is_empty(self):
        ax = editor.Accessibility.__new__(editor.Accessibility)
        ax.value = Mock(side_effect=lambda _, name: {"AXValue": 1, "AXNumberOfCharacters": 2}[name])
        ax.number = Mock(side_effect=lambda raw: 11 if raw == 2 else None)
        ax.name = Mock(side_effect=lambda name: name)
        ax.string = Mock(side_effect=lambda raw: "00:00:02:00" if raw == 42 else "")
        ax.release = Mock()

        def ax_range(kind, location):
            self.assertEqual(kind, 4)
            bounds = C.cast(location, C.POINTER(editor.CFRange)).contents
            self.assertEqual((bounds.location, bounds.length), (0, 11))
            return 30

        def read_text(element, attribute, bounds, output):
            self.assertEqual((element, attribute, bounds), (77, "AXStringForRange", 30))
            C.cast(output, C.POINTER(C.c_void_p)).contents.value = 42
            return 0

        ax.create_ax_value = Mock(side_effect=ax_range)
        ax.copy_parameterized_value = Mock(side_effect=read_text)
        ax.values = []

        self.assertEqual(ax.duration_value(77), 60)
        self.assertEqual(ax.values, [42])
        ax.release.assert_called_once_with(30)

    def test_duration_refuses_unbounded_text_ranges(self):
        ax = editor.Accessibility.__new__(editor.Accessibility)
        ax.value = Mock(return_value=1)
        ax.number = Mock(return_value=17)
        ax.create_ax_value = Mock()
        ax.copy_parameterized_value = Mock()
        self.assertEqual(ax.control_text(77), "")
        ax.create_ax_value.assert_not_called()
        ax.copy_parameterized_value.assert_not_called()

    def test_playlist_model_is_checked_before_each_change_and_after_restore(self):
        ui, evidence = Mock(), Mock()
        evidence.press.return_value = 0
        ui.menu_item.return_value = "Selection Tool"
        ui.duration_dialog.side_effect = [
            ("Cancel", (60, 60, 30)), ("Cancel", (60, 60, 29)), ("Cancel", (60, 60, 30)),
        ]

        editor.refresh_third_playlist(ui, evidence)

        self.assertEqual(ui.duration_dialog.call_args_list, [
            call("shorten", evidence), call("restore", evidence), call("verify-restoration", evidence),
        ])
        self.assertEqual(ui.seek_and_select_third_clip.call_args_list, [
            call(evidence, "shorten"), call(evidence, "restore"), call(evidence, "verify-restoration"),
        ])
        self.assertEqual(ui.resize_selected_end.call_args_list, [
            call("shorten", 0, evidence), call("restore", 2, evidence),
        ])
        self.assertEqual(evidence.press.call_args_list, [
            call("selection-tool", "Selection Tool"), call("shorten-close", "Cancel"),
            call("restore-close", "Cancel"), call("verify-restoration-close", "Cancel"),
        ])

    def test_unapplied_trim_never_restores_the_clip(self):
        ui, evidence = Mock(), Mock()
        evidence.press.return_value = 0
        ui.menu_item.return_value = "Selection Tool"
        ui.duration_dialog.return_value = ("Cancel", (60, 60, 30))

        with self.assertRaisesRegex(ValueError, "restore model"):
            editor.refresh_third_playlist(ui, evidence)

        ui.resize_selected_end.assert_called_once_with("shorten", 0, evidence)
        self.assertEqual(evidence.press.call_args_list, [
            call("selection-tool", "Selection Tool"), call("shorten-close", "Cancel"),
        ])

    def test_resize_moves_from_inclusive_clip_end_to_the_exclusive_out_point(self):
        ui = editor.EditorUI(Mock(), lambda: None)
        ui.menu_item = Mock(side_effect=["clip end", "forward", "forward", "resize end"])
        evidence = Mock()
        evidence.press.return_value = 0

        ui.resize_selected_end("restore", 2, evidence)

        self.assertEqual(ui.menu_item.call_args_list, [
            call("Go to Clip End", evidence, "restore-clip-end", section="Monitor", submenu="Go To"),
            call("Forward 1 Frame", evidence, "restore-forward-1", section="Monitor", submenu="Seek"),
            call("Forward 1 Frame", evidence, "restore-forward-2", section="Monitor", submenu="Seek"),
            call("Resize Item End", evidence, "restore-resize-end", section="Sequence", submenu="Current Item"),
        ])
        self.assertEqual(evidence.press.call_args_list, [
            call("restore-clip-end", "clip end"), call("restore-forward-1", "forward"),
            call("restore-forward-2", "forward"), call("restore-resize-end", "resize end"),
        ])

    def test_resize_fails_closed_when_navigation_is_uncertain(self):
        ui = editor.EditorUI(Mock(), lambda: None)
        ui.menu_item = Mock(return_value="clip end")
        evidence = Mock()
        evidence.press.return_value = editor.CANNOT_COMPLETE

        with self.assertRaisesRegex(ValueError, "clip's end was not acknowledged"):
            ui.resize_selected_end("shorten", 0, evidence)

        evidence.press.assert_called_once_with("shorten-clip-end", "clip end")

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


    def test_third_clip_selection_uses_native_project_navigation_without_a_clock(self):
        ui = editor.EditorUI(Mock(), lambda: None)
        ui.await_target = Mock(side_effect=["Project Monitor radio", "project-monitor", "V1 AXButton"])
        ui.menu_item = Mock(side_effect=["Sequence effects", "Go to Project End", "Rewind 1 Frame", "Select Clip"])
        evidence = Mock()

        evidence.press.return_value = 0

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
            call("select-video-track-one", "V1 AXButton"),
            call("select-third", "Select Clip"),
        ])

    def test_missing_verified_v1_button_stops_before_opening_the_import_menu(self):
        ui, evidence = Mock(), Mock()
        ui.await_target.side_effect = TimeoutError("native V1 button unverifiable")

        with self.assertRaisesRegex(TimeoutError, "native V1 button unverifiable"):
            editor.import_and_save(ui, evidence, Path("timeline.otio"), Path("initial.kdenlive"), Mock())

        ui.menu_item.assert_not_called()
        evidence.press.assert_not_called()


if __name__ == "__main__":
    unittest.main()
