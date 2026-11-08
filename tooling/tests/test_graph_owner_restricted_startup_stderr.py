"""Source-only custody gate for Graph restricted owner startup diagnostics.

No Semwright process, Graph case, Native effect or G2 is executed.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

TOOLING = Path(__file__).resolve().parents[1]
OWNER = TOOLING / "native_graph_browser_owner.py"
SHARED = TOOLING / "native_host_shared_state.py"


class RestrictedOwnerStartupStderrTests(unittest.TestCase):
    def test_only_restricted_owner_binds_exact_private_first_party_capture(self):
        source = ast.parse(OWNER.read_text(encoding="utf-8"))
        owner = next(node for node in source.body
                     if isinstance(node, ast.ClassDef) and node.name == "GraphBrowserOwner")
        init = next(node for node in owner.body
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        selectors = [node.value for node in ast.walk(init)
                     if isinstance(node, ast.Assign)
                     and len(node.targets) == 1
                     and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id == "startup_stderr_args"]
        self.assertEqual(len(selectors), 1)
        selector = selectors[0]
        self.assertIsInstance(selector, ast.Call)
        self.assertEqual(ast.unparse(selector.func), "self.shared.driver_startup_stderr_args")
        self.assertEqual(len(selector.args), 2)
        self.assertEqual(ast.dump(selector.args[0]),
                         ast.dump(ast.parse("self.paths['native-output']", mode="eval").body))
        self.assertEqual(selector.args[1].value, "shared-state")
        self.assertEqual([(item.arg, getattr(item.value, "value", None))
                          for item in selector.keywords], [("selected", True)])
        starts = [node for node in ast.walk(init) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "start"
                  and node.args and isinstance(node.args[0], ast.Constant)]
        restricted = [node for node in starts if node.args[0].value == "owner-restricted"]
        bootstrap = [node for node in starts if node.args[0].value == "owner-bootstrap"]
        self.assertEqual((len(restricted), len(bootstrap)), (1, 1))
        self.assertIsInstance(restricted[0].args[1], ast.List)
        selected = [item for item in restricted[0].args[1].elts
                    if isinstance(item, ast.Starred)]
        self.assertEqual(len(selected), 1)
        self.assertIsInstance(selected[0].value, ast.Name)
        self.assertEqual(selected[0].value.id, "startup_stderr_args")
        self.assertFalse(any(isinstance(item, ast.Starred)
                             for item in bootstrap[0].args[1].elts))

    @unittest.skipUnless(os.name == "posix" and sys.platform == "linux",
                         "Production private custody is Linux-only")
    def test_actual_first_party_selector_is_private_once_and_no_new_grant(self):
        source = ast.parse(SHARED.read_text(encoding="utf-8"))
        candidates = [node for node in source.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "driver_startup_stderr_args"]
        self.assertEqual(len(candidates), 1)
        namespace = {"Path": Path, "os": os, "sys": sys, "stat": stat}
        exec(compile(ast.Module(body=candidates, type_ignores=[]),
                     str(SHARED), "exec"), namespace)
        selected = namespace["driver_startup_stderr_args"]
        with tempfile.TemporaryDirectory(prefix="mw-graph-stderr-g0-") as temp:
            root = Path(temp).resolve(strict=True)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(selected(root, "shared-state", selected=False), [])
            self.assertFalse((root / "startup-stderr").exists())
            args = selected(root, "shared-state", selected=True)
            expected = root / "startup-stderr" / "shared-state"
            self.assertEqual(args,
                             ["--driver-startup-stderr-dir", str(expected),
                              "--driver-startup-stderr-provider", "driver:motionwright"])
            self.assertTrue(expected.is_dir())
            self.assertEqual(stat.S_IMODE(expected.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(expected.parent.stat().st_mode), 0o700)
            with self.assertRaises(FileExistsError):
                selected(root, "shared-state", selected=True)
            with self.assertRaises(AssertionError):
                selected(root, "owner-restricted", selected=True)
            unsafe = root / "unsafe"
            unsafe.mkdir(mode=0o755)
            unsafe.chmod(0o755)
            with self.assertRaises(AssertionError):
                selected(unsafe, "shared-state", selected=True)
            self.assertFalse((unsafe / "startup-stderr").exists())


if __name__ == "__main__":
    unittest.main()
