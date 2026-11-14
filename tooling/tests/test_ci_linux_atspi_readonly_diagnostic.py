"""Read-only original AT-SPI diagnostic tests; never a product Save gate."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_linux_atspi_readonly_diagnostic as diagnostic


class ReadOnlyOriginalContracts(unittest.TestCase):
    def test_creates_one_durable_stage_and_refuses_replacement(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            diagnostic.stage(target, "one-callback-intent", {"target": "read-only"})
            item = json.loads((target / "one-callback-intent.json").read_bytes())
            self.assertEqual(item["stage"], "one-callback-intent")
            self.assertTrue(item["no_mutation"])
            self.assertEqual(item["target"], "read-only")
            with self.assertRaises(FileExistsError):
                diagnostic.stage(target, "one-callback-intent")
            with self.assertRaisesRegex(diagnostic.ProbeError, "READ_ONLY_INVALID_STAGE"):
                diagnostic.stage(target, "invalid_underscore")
            self.assertEqual(len(list(target.iterdir())), 1)

    def test_requires_exact_source_and_disposable_linux(self):
        with self.assertRaisesRegex(diagnostic.ProbeError, "READ_ONLY_SOURCE_INVALID"):
            diagnostic.admit_source(ROOT, "not-a-sha", "a" * 40)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(diagnostic.ProbeError, "READ_ONLY_DISPOSABLE_LINUX_REQUIRED"):
                diagnostic.admit_source(ROOT, "a" * 40, "a" * 40)

    def test_no_editor_or_native_effect_callable_in_probe_ast(self):
        tree = ast.parse((ROOT / "tooling/ci_linux_atspi_readonly_diagnostic.py").read_bytes())
        forbidden = {"set_text", "invoke", "set_text_contents", "do_action", "exec"}
        invoked = {node.func.attr for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(invoked & forbidden)
        funcs = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        self.assertEqual(funcs, ["stage", "admit_source", "trace_controls", "main"])

    def test_source_is_pinned_to_original_built_client(self):
        original = "21a1a41510f44b480bc4264272479d48048a8be8"
        for name in ("tooling/linux_first_use_accessible.py", "tooling/linux_gui_probe.py"):
            with self.subTest(path=name):
                old = subprocess.check_output(
                    ["git", "-C", str(ROOT), "rev-parse", original + ":" + name],
                    text=True).strip()
                new = subprocess.check_output(
                    ["git", "-C", str(ROOT), "rev-parse", "HEAD:" + name],
                    text=True).strip()
                self.assertEqual(old, new)

    def test_shallow_checkout_admission_uses_pinned_blobs_not_historical_commit(self):
        source = "b" * 40
        original = "a" * 40
        blobs = {
            "tooling/linux_first_use_accessible.py": "e9b534554eb918df8d831d41fc10d45a4a6f4a9c",
            "tooling/linux_gui_probe.py": "d9afd40d628132290e044118cd87b0064631250d",
        }
        observed = []
        def git(argv, **kwargs):
            observed.append(tuple(argv[-2:]))
            value = argv[-1]
            if value == "HEAD":
                return source + "\n"
            for path, digest in blobs.items():
                if value == "HEAD:" + path:
                    return digest + "\n"
            raise AssertionError("historical checkout fetch was requested")
        environment = {
            "GITHUB_ACTIONS": "true", "GITHUB_SHA": source,
            "DISPLAY": ":99", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/fixture",
            "ORIGINAL_ATSPI_CLIENT_BLOB": blobs["tooling/linux_first_use_accessible.py"],
            "ORIGINAL_GUI_PROBE_BLOB": blobs["tooling/linux_gui_probe.py"],
        }
        with patch.dict(os.environ, environment), \
             patch.object(diagnostic.sys, "platform", "linux"), \
             patch.object(diagnostic.subprocess, "check_output", side_effect=git):
            diagnostic.admit_source(ROOT, source, original)
        self.assertEqual(len(observed), 3)

    def test_isolated_workflow_has_no_build_or_effect_and_one_artifact_pin(self):
        raw = (ROOT / ".github/workflows/linux-atspi-readonly-original.yml").read_text(encoding="utf8")
        self.assertIn("14103859547", raw)
        self.assertIn("5796abb450e67b294d8c6b0ff1d4cbbc8ae1944af7fa51cb297f3f71774eebdb", raw)
        self.assertIn("43e0e59a4822f819c8cb75ef7e8f4ac80ce9d09945b81e24ea6b4ab8bdfef08c", raw)
        self.assertIn("run-id: 43570033851", raw)
        self.assertIn("ORIGINAL_ATSPI_CLIENT_BLOB", raw)
        self.assertIn("ORIGINAL_GUI_PROBE_BLOB", raw)
        self.assertNotIn("fetch-depth: 0", raw)
        self.assertIn("timeout -k 5s 65s", raw)
        self.assertIn('at-spi2-core libglib2.0-bin', raw)
        self.assertIn('dbus-run-session', raw)
        self.assertIn("on:\n  push:\n", raw)
        self.assertIn("if: always()", raw)
        for forbidden in ("tauri build", "cargo build", "gh release", "gh workflow run"):
            self.assertNotIn(forbidden, raw)


if __name__ == "__main__":
    unittest.main()
