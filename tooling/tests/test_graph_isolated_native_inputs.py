"""Real isolated-Python import controls; no Native or network execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CHILD = r"""
import hashlib, json, pathlib, sys, types
assert sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1
repo, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
path = repo / "tooling/ci_canvas_service.py"
gate = types.ModuleType("isolated_graph_gate")
gate.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), gate.__dict__)
names = ("ci_graph_dev_native_startup", "native_graph_executable_inputs")
pins = {}
for name in names:
    relative = "tooling/" + name + ".py"
    raw = (repo / relative).read_bytes()
    pins[relative] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
if mode == "bad-digest":
    pins["tooling/native_graph_executable_inputs.py"]["sha256"] = "0" * 64
elif mode == "missing-pin":
    del pins["tooling/ci_graph_dev_native_startup.py"]
elif mode == "foreign-preloaded":
    foreign = types.ModuleType(names[1])
    foreign.__file__ = str(pathlib.Path.cwd() / (names[1] + ".py"))
    sys.modules[names[1]] = foreign
elif mode == "same-origin-preloaded":
    for name in names:
        old = types.ModuleType(name)
        old.__file__ = str(repo / "tooling" / (name + ".py"))
        old.unverified_cached_value = True
        sys.modules[name] = old
missing = object()
before = {name: sys.modules.get(name, missing) for name in names}
search_path = tuple(sys.path)
reads = []
def remember(ref, maximum):
    result = gate.graph_read(ref, maximum)
    reads.append(pathlib.Path(ref["path"]).name)
    return result
denied = None
try:
    helper = gate.graph_load_native_input_verifier(repo, pins, remember)
except (ValueError, KeyError) as error:
    denied = type(error).__name__
if mode in ("bad-digest", "missing-pin", "foreign-preloaded"):
    assert denied is not None, "unbound dependency was admitted"
else:
    assert denied is None
    assert pathlib.Path(helper.__file__) == repo / "tooling/native_graph_executable_inputs.py"
    assert helper.stage_executable_input.__module__ == "ci_graph_dev_native_startup"
    assert callable(helper.stage_executable_input) and callable(helper.validate_live)
    assert helper.LIMITS["individual_bytes"] == 64 * 1024 * 1024
    assert not hasattr(helper, "unverified_cached_value")
    assert helper is not before[names[1]]
    assert reads == ["ci_graph_dev_native_startup.py", "native_graph_executable_inputs.py"]
    try:
        helper.need(False, "inert-focal-denial")
    except ValueError as error:
        assert str(error) == "inert-focal-denial"
    else:
        raise AssertionError("actual verifier guard did not execute")
assert tuple(sys.path) == search_path
assert all(sys.modules.get(name, missing) is before[name] for name in names)
assert not (pathlib.Path.cwd() / "UNTRUSTED_IMPORT_EXECUTED").exists()
print(json.dumps({"mode": mode, "isolated": True, "source_reads": reads,
                  "denied": denied, "search_path_unchanged": True,
                  "module_bindings_restored": True, "native_calls": 0}))
"""


class GraphIsolatedNativeInputTests(unittest.TestCase):
    def run_isolated(self, mode):
        with tempfile.TemporaryDirectory(prefix="graph-isolated-import-") as directory:
            cwd = Path(directory)
            poison = "from pathlib import Path\nPath('UNTRUSTED_IMPORT_EXECUTED').touch()\nraise RuntimeError('foreign import executed')\n"
            for name in ("ci_graph_dev_native_startup", "native_graph_executable_inputs"):
                (cwd / (name + ".py")).write_text(poison, encoding="utf-8")
            process = subprocess.run([sys.executable, "-I", "-B", "-c", CHILD, str(ROOT), mode],
                cwd=cwd, env={**os.environ, "PYTHONPATH": str(cwd)},
                capture_output=True, text=True, timeout=20)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertTrue(result["isolated"])
            self.assertTrue(result["search_path_unchanged"])
            self.assertTrue(result["module_bindings_restored"])
            self.assertEqual(result["native_calls"], 0)
            self.assertFalse((cwd / "UNTRUSTED_IMPORT_EXECUTED").exists())
            return result

    def test_fresh_isolated_process_loads_actual_verified_transitive_sources(self):
        self.assertIsNone(self.run_isolated("fresh")["denied"])

    def test_wrong_source_digest_denies_before_import(self):
        self.assertEqual(self.run_isolated("bad-digest")["denied"], "ValueError")

    def test_missing_transitive_source_cannot_fall_back_to_pythonpath(self):
        self.assertEqual(self.run_isolated("missing-pin")["denied"], "KeyError")

    def test_foreign_preloaded_module_is_denied(self):
        self.assertEqual(self.run_isolated("foreign-preloaded")["denied"], "ValueError")

    def test_same_path_cached_object_cannot_replace_verified_code(self):
        self.assertIsNone(self.run_isolated("same-origin-preloaded")["denied"])


if __name__ == "__main__":
    unittest.main()
