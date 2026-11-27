"""Test the bounded installed-editor route without macOS UI effects."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call

import kdenlive_macos_editor as editor


class MacEditorRouteTests(unittest.TestCase):
    def test_one_import_and_save_to_exact_paths(self):
        ui = Mock()
        ui.menu_item.side_effect = ["import item", "save item"]
        ui.file_panel.side_effect = [("input field", "Open"), ("output field", "Save")]
        evidence = Mock()
        original = Path("original") / "timeline.otio"
        initial = Path("evidence") / "initial.kdenlive"

        editor.import_and_save(ui, evidence, original, initial, lambda: None)

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

if __name__ == "__main__":
    unittest.main()
