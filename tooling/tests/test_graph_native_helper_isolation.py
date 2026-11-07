"""Regression for source-pinned Graph native helpers under a fresh Python -I wait.

This is source-only: no R74 approval, Native Host, Store, or effects are created.
"""
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
_CHILD = r'''
import hashlib
import importlib.util
from pathlib import Path
import sys
import types

repo = Path(sys.argv[1])
mode = sys.argv[2]
assert sys.flags.isolated == 1
assert str(repo / "tooling") not in sys.path
assert str(repo) not in sys.path
before_path = list(sys.path)
for name in ("ci_graph_dev_native_startup", "native_graph_executable_inputs"):
    assert name not in sys.modules
spec = importlib.util.spec_from_file_location("graph_isolated_wait_under_test",
                                               repo / "tooling/ci_canvas_service.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
files = {}
for name in ("ci_graph_dev_native_startup", "native_graph_executable_inputs"):
    raw = (repo / "tooling" / (name + ".py")).read_bytes()
    files["tooling/" + name + ".py"] = {"bytes": len(raw),
                                            "sha256": hashlib.sha256(raw).hexdigest()}
if mode == "bad_dependency_pin":
    files["tooling/ci_graph_dev_native_startup.py"]["sha256"] = "0" * 64
if mode == "bad_native_pin":
    files["tooling/native_graph_executable_inputs.py"]["sha256"] = "0" * 64
if mode == "foreign_preloaded":
    marker = types.ModuleType("ci_graph_dev_native_startup")
    sys.modules["ci_graph_dev_native_startup"] = marker
    try:
        gate.graph_load_native_input_helpers(repo, files)
    except ValueError:
        pass
    else:
        raise AssertionError("foreign preloaded helper was accepted")
    assert sys.modules["ci_graph_dev_native_startup"] is marker
else:
    if mode.startswith("bad_"):
        try:
            gate.graph_load_native_input_helpers(repo, files)
        except ValueError:
            pass
        else:
            raise AssertionError("modified pinned helper was accepted")
    else:
        for _ in range(2):
            native = gate.graph_load_native_input_helpers(repo, files)
            assert native.__file__ == str(repo / "tooling/native_graph_executable_inputs.py")
            assert callable(native.validate_live)
            assert callable(native.prepare)
            assert native.ROLES["native_provider"] == "motionwright-native-provider"
            assert "ci_graph_dev_native_startup" not in sys.modules
            assert "native_graph_executable_inputs" not in sys.modules
assert sys.path == before_path
print("ISOLATED_GRAPH_NATIVE_HELPERS_OK:" + mode)
'''


class GraphNativeHelperIsolationTests(unittest.TestCase):
    def run_child(self, mode):
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _CHILD, str(ROOT), mode],
            cwd=ROOT, capture_output=True, text=True, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertIn("ISOLATED_GRAPH_NATIVE_HELPERS_OK:" + mode, result.stdout)

    def test_fresh_isolated_wait_loads_both_source_pinned_helpers(self):
        self.run_child("valid")

    def test_changed_original_dependency_digest_is_rejected(self):
        self.run_child("bad_dependency_pin")

    def test_changed_original_native_helper_digest_is_rejected(self):
        self.run_child("bad_native_pin")

    def test_preloaded_foreign_helper_is_rejected(self):
        self.run_child("foreign_preloaded")


if __name__ == "__main__":
    unittest.main()
