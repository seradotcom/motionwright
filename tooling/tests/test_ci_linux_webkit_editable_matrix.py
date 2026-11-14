"""Source-only safety and HTML structure tests for WebKitGTK interface matrix."""
import ast
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import os

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tooling'))
import ci_linux_webkit_editable_matrix as sample


class InterfaceMatrixSource(unittest.TestCase):
    def test_fixed_five_semantically_named_containers(self):
        self.assertEqual(len(sample.EXPECTED),5)
        for label in sample.EXPECTED[:-1]:
            self.assertIn('aria-label="'+label+'"',sample.HTML)
        self.assertIn('<input id="plain" type="text"',sample.HTML)
        self.assertIn('<textarea id="multi"',sample.HTML)
        self.assertIn('contenteditable="true"',sample.HTML)
        self.assertIn('role="textbox"',sample.HTML)
        self.assertIn("GTK native entry",sample.EXPECTED)

    def test_no_editing_api_present_in_diagnostic(self):
        tree=ast.parse((ROOT/'tooling/ci_linux_webkit_editable_matrix.py').read_bytes())
        names={node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)}
        self.assertFalse(names & {"do_action","generate_keyboard_event","set_text_contents","grab_focus","invoke","set_text"})
        self.assertNotIn('xdotool', (ROOT/'tooling/ci_linux_webkit_editable_matrix.py').read_text())

    def test_original_records_are_exclusive_and_immutable(self):
        with tempfile.TemporaryDirectory() as dirname:
            output=Path(dirname)/"matrix.json"
            sample.record(output,{"schema":"fixture","status":"READ_ONLY"})
            self.assertTrue(output.is_file())
            with self.assertRaisesRegex(sample.ProbeError,"MATRIX_ORIGINAL_EXISTS"):
                sample.record(output,{"schema":"fixture","status":"BAD"})
            self.assertEqual(output.read_text().count("READ_ONLY"),1)

    def test_enumerated_public_names_only(self):
        self.assertIn("EditableText",sample.KNOWN)
        self.assertIn("Text",sample.KNOWN)
        self.assertNotIn("Project title",sample.HTML)
        self.assertNotIn("Create project",sample.HTML)

    def test_runtime_requires_disposable_linux(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS":"false"}):
            with self.assertRaisesRegex(sample.ProbeError,"DISPOSABLE"):
                sample.enable_session()

    def test_path_scoped_workflow_has_no_product_build_or_effect(self):
        raw=(ROOT/'.github/workflows/linux-webkit-html-editable-source.yml').read_text()
        self.assertIn('on:\n  push:',raw)
        self.assertIn("github.run_attempt == 1",raw)
        self.assertIn('gir1.2-webkit2-4.1',raw)
        self.assertIn('gir1.2-atspi-2.0',raw)
        self.assertIn('timeout -k 5s 75s',raw)
        self.assertIn('if: always()',raw)
        for forbidden in ("tauri build","cargo build","gh release","gh workflow run",
                          "tooling/linux_first_use_accessible.py installed"):
            self.assertNotIn(forbidden,raw)


if __name__=="__main__":
    unittest.main()
