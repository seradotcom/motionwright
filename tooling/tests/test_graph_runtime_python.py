"""Source/installed-host controls for the Graph system Python ABI; no Native call."""
import ast
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "tooling/ci_canvas_service.py"
SPEC = importlib.util.spec_from_file_location("graph_python_gate", PATH)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class GraphRuntimePythonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PATH.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        cls.functions = {n.name: ast.get_source_segment(cls.source, n) for n in tree.body
                         if isinstance(n, ast.FunctionDef)}
        cls.workflow = (ROOT / ".github/workflows/studio-service-verification.yml").read_text(encoding="utf-8")

    def test_workflow_selects_only_graph_native_interpreter(self):
        block = self.workflow.split("- name: Produce the original Graph builds and private R74 review carrier", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("if: matrix.selection == 'phase1-native-graph'", block)
        self.assertIn("MOTIONWRIGHT_GRAPH_RUNTIME_PYTHON: /usr/bin/python3", block)
        self.assertIn("python -I -B tooling/ci_canvas_service.py graph produce --repo .", block)
        self.assertIn("python-version: '3.13'", self.workflow)
        self.assertEqual(self.workflow.count("MOTIONWRIGHT_GRAPH_RUNTIME_PYTHON:"), 1)

    def test_runtime_and_native_manifest_share_selected_system_binary(self):
        producer = self.functions["graph_produce"]
        self.assertIn("runtime_python = graph_runtime_python()", producer)
        self.assertIn("SEMWRIGHT_TEST_PYTHON=str(runtime_python)", producer)
        self.assertIn('graph_command(env, [str(runtime_python), "-I", "-B"', producer)
        self.assertNotIn("SEMWRIGHT_TEST_PYTHON=str(Path(sys.executable)", producer)
        # The outer controller's hashed cryptography bootstrap remains isolated.
        self.assertIn('graph_command(env, [sys.executable, "-m", "pip", "install"', producer)
        self.assertIn('"--require-hashes"', producer)

    def test_runtime_wheels_and_imports_are_bound_to_bootstrap_abi(self):
        runtime = self.functions["graph_runtime_inner"]
        self.assertIn('graph_command(env, [sys.executable, "-m", "pip", "download"', runtime)
        self.assertIn('graph_command(env, [sys.executable, "-m", "pip", "install"', runtime)
        self.assertIn('"--target", str(runtime / "site-packages")', runtime)
        self.assertLess(runtime.index("python_abi = graph_runtime_python_abi()"), runtime.index('"-m", "pip"'))
        self.assertIn('wheel.name.split("-")[2:4] == [python_abi["wheel_python_tag"], python_abi["wheel_abi_tag"]]', runtime)
        self.assertIn('graph_runtime_inventory(repo, runtime, producer)', runtime)
        self.assertNotIn("--break-system-packages", runtime)
        self.assertIn('"native_render_performed": False, "profile_admitted": False', runtime)

    def test_bootstrap_identity_is_rechecked_and_retained_in_inventory(self):
        inventory = self.functions["graph_runtime_inventory"]
        self.assertIn('"python-bootstrap.original.json"', inventory)
        self.assertIn('python_abi = graph_runtime_python_abi()', inventory)
        self.assertIn('"interpreter": graph_ref(Path(python_abi["executable"]), 64 * GRAPH_BYTES)', inventory)
        self.assertIn('"python_abi": python_abi', inventory)
        self.assertIn('Observed Python differs from the selected Driver Host interpreter', inventory)
        self.assertIn('Installed Manim/PyAV versions differ from runtime schema 2', inventory)
        self.assertIn('"native_render_performed": False', inventory)

    def test_unselected_or_toolcache_python_is_rejected_before_execution(self):
        for selected in (None, "", "/opt/hostedtoolcache/Python/3.13.17/x64/bin/python3.13",
                         "/usr/bin/python3.12", "/tmp/python3"):
            with self.subTest(selected=selected), mock.patch.dict(os.environ, {}, clear=True):
                if selected is not None:
                    os.environ["MOTIONWRIGHT_GRAPH_RUNTIME_PYTHON"] = selected
                with self.assertRaises(ValueError):
                    gate.graph_runtime_python()

    @unittest.skipUnless(sys.platform == "linux", "Installed system interpreter identity requires Linux")
    def test_installed_host_must_match_exact_ubuntu_abi(self):
        with mock.patch.dict(os.environ, {"MOTIONWRIGHT_GRAPH_RUNTIME_PYTHON": "/usr/bin/python3"}):
            selected = Path("/usr/bin/python3").resolve(strict=True)
            if selected.name != "python3.12":
                with self.assertRaises(ValueError):
                    gate.graph_runtime_python()
                return
            if Path(sys.executable).resolve(strict=True) != selected:
                with self.assertRaises(ValueError):
                    gate.graph_runtime_python_abi()
                return
            actual = gate.graph_runtime_python_abi()
            self.assertEqual(actual["soabi"], "cpython-312-x86_64-linux-gnu")
            self.assertEqual(actual["stdlib"], "/usr/lib/python3.12")
            self.assertEqual(actual["version_info"][:2], [3, 12])


if __name__ == "__main__":
    unittest.main()
