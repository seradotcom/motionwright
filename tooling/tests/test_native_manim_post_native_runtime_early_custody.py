"""A4 regression: preserve actual post-Native runtime identity before any readback.

This uses only a copied Python function with inert I/O stubs. No Native
invocation, project mutation, key, ciphertext, renderer, or G2 assertion.
"""
import ast
from pathlib import Path
import sys
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "native_manim_recovery_cycle.py"


class SourceReadbackFailed(Exception):
    pass


class EarlyPostNativeCustodyTests(unittest.TestCase):
    def finish_phase(self, recorded, runtime_identity):
        source = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(SOURCE))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "finish_phase"]
        self.assertEqual(len(functions), 1)
        module = ast.Module(body=functions, type_ignores=[])
        gate = types.ModuleType("native_manim_recovery_gate")
        gate.check_runtime = lambda current: runtime_identity
        previous = sys.modules.get(gate.__name__)
        sys.modules[gate.__name__] = gate
        self.addCleanup(
            lambda: sys.modules.pop(gate.__name__, None)
            if previous is None else sys.modules.__setitem__(gate.__name__, previous)
        )
        native = {"native_manim_e2e": "PASS"}
        def require(condition, code):
            if not condition:
                raise ValueError(code)
        def retain_source(*args, **kwargs):
            raise SourceReadbackFailed("First original source readback failed")
        namespace = {
            "PHASES": ("original-reference", "reopen-original-reference",
                       "edited", "reopen-edited"),
            "EVIDENCE_NAME": "motionwright-manim-evidence.json",
            "read_object": lambda path: native,
            "require": require,
            "private_json": lambda path, value: recorded.append((path.name, value)),
            "retained_source": retain_source,
            "Path": Path,
        }
        exec(compile(module, str(SOURCE), "exec"), namespace)
        def attempt():
            return namespace["finish_phase"](
                {"phase": "original-reference", "current": {"source_sha": "original"},
                 "runtime_binding": {"source": "same-runtime"}},
                {}, native, Path("E:/A4/readback-evidence"),
                Path("E:/A4/output"), Path("E:/A4/work"),
                Path("E:/A4/reopened.sqlite3"), {}, capture=None,
                source_binary=None, python=None, runtime=None, tooling=None,
                launch=None,
            )
        return attempt

    def test_post_native_runtime_observation_survives_first_source_readback_error(self):
        retained = []
        attempt = self.finish_phase(retained, {"source": "same-runtime"})
        with self.assertRaisesRegex(SourceReadbackFailed, "readback failed"):
            attempt()
        self.assertEqual(retained,
                         [("frozen-runtime-binding.json", {"source": "same-runtime"})])

    def test_different_runtime_is_denied_without_releasing_a_false_observation(self):
        retained = []
        attempt = self.finish_phase(retained, {"source": "changed-runtime"})
        with self.assertRaisesRegex(ValueError, "recovery_runtime_changed_during_native_phase"):
            attempt()
        self.assertEqual(retained, [])


if __name__ == "__main__":
    unittest.main()
