"""One AT-SPI keyboard event into a standalone fixture; never product G2 credit."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/"tooling")]
import ci_linux_webkit_atspi_keyboard_fixture as fixture
from linux_first_use_accessible import ProbeError


class OneFixtureKeyboardEvent(unittest.TestCase):
    def test_constant_single_input_and_no_product_mutation_calls(self):
        s=(ROOT/"tooling/ci_linux_webkit_atspi_keyboard_fixture.py").read_text()
        tree=ast.parse(s)
        attr_calls=[node.func.attr for node in ast.walk(tree)
                    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
        self.assertEqual(attr_calls.count("generate_keyboard_event"),0)
        self.assertEqual(s.count("atspi.generate_keyboard_event, 0, VALUE"),1)
        self.assertEqual(s.count("atspi.Component.grab_focus, title.node"),1)
        for name in ("set_text_contents", "do_action(", "eval(",
                     "subprocess.run([\"xdotool\"", "import XTest", "from Xlib"):
            self.assertNotIn(name,s)
        self.assertLess(s.index('mark("type-intent"'),s.index('atspi.generate_keyboard_event'))
        self.assertIn("product_creates",s)
        self.assertIn("no retry",s.lower())

    def test_scope_cannot_run_outside_original_disposable_session(self):
        with tempfile.TemporaryDirectory() as dirname, patch.dict(
            os.environ, {"GITHUB_ACTIONS":"false"}):
            p=Path(dirname)
            with self.assertRaisesRegex(ProbeError,"ORIGINAL_LINUX_REQUIRED"):
                fixture.admission(p/"result.json",p/"ready.json")

    def test_effect_markers_are_create_only(self):
        from ci_linux_webkit_editable_matrix import record
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/"type-intent.json"
            record(path,{"phase":"TYPE_UNKNOWN"})
            with self.assertRaisesRegex(ProbeError,"MATRIX_ORIGINAL_EXISTS"):
                record(path,{"phase":"TYPE_UNKNOWN"})
            self.assertIn("TYPE_UNKNOWN",path.read_text())

    def test_one_registered_event_after_focus_and_same_owner(self):
        current=SimpleNamespace(focused=False,text="")
        def focus(node):
            self.assertIs(node,current)
            current.focused=True
            return True
        def synth(keyval,content,method):
            self.assertEqual(keyval,0)
            self.assertEqual(content,fixture.VALUE)
            self.assertEqual(method,7)
            self.assertTrue((root/"type-intent.json").is_file())
            current.text=content
            sent.append(1)
            return True
        class State:
            def contains(self,value):return current.focused and value==3
        current.get_state_set=lambda:State()
        class Bridge:
            def pid(self,app):return 97
            def owner_pid(self,node):return 97
            def call(self,fn,*args):return fn(*args)
            def read_text(self,node):return node.text
        api=SimpleNamespace(Component=SimpleNamespace(grab_focus=focus),
                            StateType=SimpleNamespace(FOCUSED=3),
                            KeySynthType=SimpleNamespace(STRING=7),
                            generate_keyboard_event=synth)
        sent=[]
        with tempfile.TemporaryDirectory() as dirname:
            root=Path(dirname)
            result=fixture.single_input(
                Bridge(),api,SimpleNamespace(MainContext=None),object(),
                SimpleNamespace(node=current),97,root)
            self.assertEqual(result["status"],"TEXT_VERIFIED_NO_CREATE")
            self.assertEqual(result["product_creates"],0)
            self.assertEqual(len(sent),1)
            self.assertTrue((root/"focus-intent.json").exists())
            self.assertTrue((root/"focus-return.json").exists())
            self.assertTrue((root/"type-return.json").exists())
            self.assertTrue((root/"readback.json").exists())

    def test_false_focus_never_sends_keyboard_event(self):
        node=SimpleNamespace(text="")
        class Bridge:
            def pid(self,app):return 97
            def owner_pid(self,obj):return 97
            def call(self,fn,*args):return fn(*args)
        api=SimpleNamespace(Component=SimpleNamespace(grab_focus=lambda node:False))
        with tempfile.TemporaryDirectory() as dirname:
            root=Path(dirname)
            with self.assertRaisesRegex(ProbeError,"FOCUS_NOT_ACKNOWLEDGED"):
                fixture.single_input(
                    Bridge(),api,None,object(),SimpleNamespace(node=node),97,root)
            self.assertTrue((root/"focus-intent.json").is_file())
            self.assertFalse((root/"type-intent.json").exists())

    def test_workflow_single_attempt_fixture_not_product(self):
        raw=(ROOT/".github/workflows/linux-webkit-atspi-keyboard-fixture.yml").read_text()
        self.assertIn('on:\n  push:',raw)
        self.assertIn('github.run_attempt == 1',raw)
        self.assertIn('timeout -k 5s 70s',raw)
        self.assertIn('if: always()',raw)
        for term in ("tauri build","cargo build","gh workflow run",
                     "linux_first_use.py installed","xdotool","XTest"):
            self.assertNotIn(term,raw)


if __name__=="__main__":
    unittest.main()
