"""One-shot source-only contract for authentic Graph restricted daemon diagnostics.

No Host, sandbox, SQLite, reviewer, Graph run, or credential is launched here.
"""
import ast
import os
from pathlib import Path
import tempfile
import unittest

from tooling import native_graph_browser_owner as graph


class PrivateCapture:
    def __init__(self, replacement=None, create=True):
        self.calls = []
        self.replacement = replacement
        self.create = create

    def driver_startup_stderr_args(self, owner, label, *, selected):
        self.calls.append((owner, label, selected))
        directory = owner / "startup-stderr" / label
        if self.create:
            directory.parent.mkdir(mode=0o700)
            directory.mkdir(mode=0o700)
        canonical = ["--driver-startup-stderr-dir", str(directory),
                     "--driver-startup-stderr-provider", "driver:motionwright"]
        return self.replacement if self.replacement is not None else canonical


class GraphOwnerRestrictedStartupStderrTests(unittest.TestCase):
    def test_exact_private_sdk_capture_once_before_restricted_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp).resolve()
            bridge = PrivateCapture()
            result = graph.graph_restricted_startup_stderr_args(bridge, owner)
            self.assertEqual(bridge.calls, [(owner, "shared-state", True)])
            self.assertEqual(result, ["--driver-startup-stderr-dir",
                str(owner / "startup-stderr/shared-state"),
                "--driver-startup-stderr-provider", "driver:motionwright"])
            directory = owner / "startup-stderr/shared-state"
            self.assertTrue(directory.is_dir() and not directory.is_symlink())
            # Windows st_mode does not encode POSIX private ACL bits.
            if os.name == "posix":
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)

    def test_no_unreviewed_stderr_sink_or_missing_private_directory(self):
        for bridge in (PrivateCapture(["--driver-startup-stderr-dir", "/tmp/leaked",
                                      "--driver-startup-stderr-provider", "driver:motionwright"]),
                       PrivateCapture(create=False)):
            with self.subTest(bridge=repr(bridge)):
                with tempfile.TemporaryDirectory() as tmp:
                    with self.assertRaises(graph.GraphOwnerFailure):
                        graph.graph_restricted_startup_stderr_args(bridge, Path(tmp).resolve())

    def test_restricted_daemon_argv_attaches_only_exact_source_owned_capture(self):
        tree = ast.parse(Path(graph.__file__).read_text(encoding="utf-8"))
        starts = [call for call in ast.walk(tree)
                  if isinstance(call, ast.Call)
                  and isinstance(call.func, ast.Attribute)
                  and call.func.attr == "start"
                  and call.args and isinstance(call.args[0], ast.Constant)
                  and call.args[0].value == "owner-restricted"]
        self.assertEqual(len(starts), 1)
        argv = starts[0].args[1]
        self.assertIsInstance(argv, ast.List)
        self.assertTrue(any(isinstance(item, ast.Starred)
            and isinstance(item.value, ast.Name) and item.value.id == "startup_stderr_args"
            for item in argv.elts), "restricted daemon still drops private first-party capture flags")


if __name__ == "__main__":
    unittest.main()
