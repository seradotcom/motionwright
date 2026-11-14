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
        self.assertEqual(funcs, ["stage", "admit_source", "cached_application_roster",
                                 "cached_control_scope", "observe_delayed_cached_controls",
                                 "inspect_title_interface_snapshot", "read_shallow_roles",
                                 "enable_session_accessibility", "trace_controls", "main"])

    def test_source_is_pinned_to_original_built_client(self):
        original = "d72fb522089434bc6cf705a1d3410dd9759e3f04"
        for name in ("tooling/linux_first_use_accessible.py", "tooling/linux_gui_probe.py"):
            with self.subTest(path=name):
                old = subprocess.check_output(
                    ["git", "-C", str(ROOT), "rev-parse", original + ":" + name],
                    text=True).strip()
                new = subprocess.check_output(
                    ["git", "-C", str(ROOT), "rev-parse", "HEAD:" + name],
                    text=True).strip()
                self.assertEqual(old, new)

    def test_ephemeral_session_accessibility_enabled_before_app_launch(self):
        commands = []
        def reply(argv, **kwargs):
            commands.append(argv)
            value = "(<true>,)" if argv[-2:] == [
                "org.a11y.Status", "IsEnabled"] else "()"
            if "org.a11y.Bus.GetAddress" in argv:
                value = "('unix:path=/tmp/fixture',)"
            return type("Result", (), {"returncode": 0, "stdout": value})()
        env = {"GITHUB_ACTIONS": "true", "DISPLAY": ":99",
               "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/fixture"}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, env), \
             patch.object(diagnostic.subprocess, "run", side_effect=reply):
            self.assertTrue(diagnostic.enable_session_accessibility(Path(directory)))
            self.assertEqual(len(commands), 3)
            self.assertIn("org.freedesktop.DBus.Properties.Set", commands[1])
            self.assertEqual(commands[1][-1], "<true>")
            for tag in ("session-bus-activation", "session-accessibility-enable",
                        "session-accessibility-verify"):
                self.assertTrue((Path(directory)/(tag+"-return.json")).exists())
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            with self.assertRaisesRegex(diagnostic.ProbeError, "DISPOSABLE"):
                diagnostic.enable_session_accessibility(Path("unused"))

    def test_cached_application_roster_does_not_force_remote_clear(self):
        from types import SimpleNamespace
        calls = []
        root = object()
        nodes = [object()]
        class Bridge:
            desktop = root
            def call(self, function, *args):
                return function(*args)
            def children(self, node):
                self.assert_same(node)
                calls.append("cached-children")
                return nodes
            def assert_same(self, node):
                if node is not root:
                    raise AssertionError("wrong AT-SPI desktop")
            def applications(self):
                raise AssertionError("must not clear live accessible cache")
        class Desktop:
            def set_cache_mask(self, value):
                calls.append(("set-cache-mask", value))
        bridge = Bridge()
        bridge.desktop = Desktop()
        def children(node):
            if node is not bridge.desktop:
                raise AssertionError("wrong desktop in cached children")
            calls.append("cached-children")
            return nodes
        bridge.children = children
        api = SimpleNamespace(Cache=SimpleNamespace(ALL=777))
        with tempfile.TemporaryDirectory() as directory:
            observed = diagnostic.cached_application_roster(
                bridge, api, Path(directory))
            self.assertEqual(observed, nodes)
            self.assertEqual(calls, [("set-cache-mask", 777), "cached-children"])
            for name in ("accessible-cache-enable-start", "accessible-cache-enable-return",
                         "applications-start", "applications-return"):
                self.assertTrue((Path(directory)/(name+".json")).exists())

    def test_cache_excessive_roster_fails_without_selector(self):
        from types import SimpleNamespace
        class Bridge:
            desktop = SimpleNamespace(set_cache_mask=lambda value: None)
            def call(self, method, *args): return method(*args)
            def children(self, node): return [object() for _ in range(129)]
            def applications(self): raise AssertionError("clear_cache forbidden")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(diagnostic.ProbeError,
                                        "READ_ONLY_APPLICATION_LIMIT"):
                diagnostic.cached_application_roster(
                    Bridge(), SimpleNamespace(Cache=SimpleNamespace(ALL=1)),
                    Path(directory))

    def test_cached_control_scope_is_unique_owned_and_read_only(self):
        from types import SimpleNamespace
        from linux_first_use_accessible import View
        visible = frozenset(("visible", "showing", "enabled", "sensitive"))
        class Node:
            def __init__(self, role, name, children=(), owner=51):
                self.role, self.name = role, name
                self.children, self.owner = list(children), owner
        field = Node("entry", "Project title")
        button = Node("button", "Create project")
        dialog = Node("dialog", "Project library", [field, button])
        app = Node("other", "", [dialog])
        class Bridge:
            def view(self, node):
                return View(node, node.role, node.name, visible, frozenset())
            def children(self, node): return node.children
            def owner_pid(self, node): return node.owner
        with tempfile.TemporaryDirectory() as directory:
            result = diagnostic.cached_control_scope(
                Bridge(), app, {"pid": 51}, Path(directory))
            self.assertEqual(result["status"], "CACHED_CONTROLS_VERIFIED_READ_ONLY")
            self.assertEqual((result["dialog_count"], result["title_count"],
                              result["create_count"]), (1, 1, 1))
            self.assertEqual(result["editor_actions"], 0)
            self.assertTrue((Path(directory)/"cached-controls-verified.json").exists())
        with tempfile.TemporaryDirectory() as directory:
            two = Node("other", "", [dialog, dialog])
            with self.assertRaisesRegex(diagnostic.ProbeError, "AMBIGUOUS_DIALOG"):
                diagnostic.cached_control_scope(
                    Bridge(), two, {"pid": 51}, Path(directory))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(diagnostic.ProbeError,
                                        "READ_ONLY_DIALOG_OWNER_MISMATCH"):
                diagnostic.cached_control_scope(
                    Bridge(), app, {"pid": 52}, Path(directory))
        with tempfile.TemporaryDirectory() as directory:
            missing = Node("other", "", [])
            with self.assertRaisesRegex(diagnostic.ProbeError, "MISSING_DIALOG"):
                diagnostic.cached_control_scope(
                    Bridge(), missing, {"pid": 51}, Path(directory))

    def test_caches_async_dialog_after_two_read_only_checks(self):
        from linux_first_use_accessible import View
        visible = frozenset(("visible", "showing"))
        class Node:
            def __init__(self, role, name, children=()):
                self.role, self.name, self.children = role, name, list(children)
        title = Node("entry", "Project title")
        action = Node("button", "Create project")
        dialog = Node("dialog", "Project library", (title, action))
        app = Node("other", "")
        loops, sleeps, visited = [], [], []
        class Bridge:
            def view(self, n): return View(n, n.role, n.name, visible, frozenset())
            def children(self, n):
                if n is app:
                    visited.append("root")
                    return [] if len(visited) < 3 else [dialog]
                return n.children
            def owner_pid(self, n): return 91
            def pid(self, n): return 91
        with tempfile.TemporaryDirectory() as tmp:
            result = diagnostic.observe_delayed_cached_controls(
                Bridge(), app, {"pid": 91}, Path(tmp),
                pause=sleeps.append, pump=lambda: loops.append("pump"))
            self.assertEqual(result["status"], "CACHED_CONTROLS_VERIFIED_READ_ONLY")
            self.assertEqual(sleeps, [1.5, 1.5])
            self.assertEqual(loops, ["pump", "pump"])
            self.assertTrue((Path(tmp)/"retry-two-controls-verified.json").is_file())
            self.assertFalse((Path(tmp)/"retry-three-app-walk-start.json").exists())

    def test_no_dialog_six_reads_exhausts_without_authority(self):
        from linux_first_use_accessible import View
        visible = frozenset(("visible", "showing"))
        class Node:
            pass
        app = Node()
        pauses=[]
        class Bridge:
            def view(self, n): return View(n, "other", "", visible, frozenset())
            def children(self, n): return []
            def pid(self, n): return 91
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(diagnostic.NotReady, "MISSING_DIALOG"):
                diagnostic.observe_delayed_cached_controls(
                    Bridge(), app, {"pid": 91}, Path(tmp), pause=pauses.append)
            self.assertEqual(pauses, [1.5]*6)
            self.assertTrue((Path(tmp)/"retry-six-dialog-count.json").is_file())
            self.assertFalse((Path(tmp)/"cached-controls-verified.json").exists())

    def test_title_interfaces_snapshot_is_read_only_and_source_bound(self):
        from types import SimpleNamespace
        from linux_first_use_accessible import View
        class Title:
            def get_interfaces(self): return ["Accessible", "Component", "Text"]
            def get_text_iface(self): return object()
            def get_editable_text_iface(self): return None
        title = Title()
        app = object()
        field = View(title, "entry", "Project title",
                     frozenset(("visible", "showing", "enabled", "editable")),
                     frozenset(("Accessible", "Text")))
        button = View(object(), "button", "Create project",
                      frozenset(("visible", "showing", "enabled")),
                      frozenset(("Action",)))
        class Bridge:
            def pid(self, node): return 107
            def call(self, fn, *args): return fn(*args)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(diagnostic, "select_controls",
                          return_value=(app, object(), field, button)) as selection:
            result = diagnostic.inspect_title_interface_snapshot(
                Bridge(), {"pid": 107}, Path(directory))
            selection.assert_called_once()
            self.assertEqual(result["status"], "TITLE_INTERFACE_SNAPSHOT_READ_ONLY")
            self.assertEqual(result["title_interface_names_refreshed"],
                             ["Accessible", "Component", "Text"])
            self.assertEqual(result["title_interface_names_cached"],
                             ["Accessible", "Text"])
            self.assertTrue(result["title_text_proxy_available"])
            self.assertFalse(result["title_editable_proxy_available"])
            self.assertTrue(result["create_action_interface_present"])
            self.assertEqual(result["editor_actions"], 0)
            self.assertTrue((Path(directory)/"title-editable-proxy-return.json").exists())
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(diagnostic, "select_controls",
                          return_value=(app, object(), field, button)):
            with self.assertRaisesRegex(diagnostic.ProbeError,
                                        "READ_ONLY_TITLE_APPLICATION_CHANGED"):
                diagnostic.inspect_title_interface_snapshot(
                    Bridge(), {"pid": 999}, Path(directory))

    def test_shallow_app_roles_are_bounded_and_names_unread(self):
        class Node:
            def __init__(self, role, children=()):
                self.role, self.children = role, list(children)
            def get_child_count(self): return len(self.children)
            def get_child_at_index(self, index): return self.children[index]
            def get_role(self): return self.role
            def get_name(self): raise AssertionError("unapproved accessible text read")
        class Bridge:
            def call(self, function, *args): return function(*args)
        app = Node(1, [Node(2, [Node(6), Node(8)]), Node(3, [Node(9)])])
        with tempfile.TemporaryDirectory() as directory:
            result = diagnostic.read_shallow_roles(Bridge(), app, Path(directory))
            self.assertEqual(result["status"], "SHALLOW_ROLE_SNAPSHOT_ONLY")
            self.assertEqual(result["root_child_count"], 2)
            self.assertEqual(result["first_roles"], [2, 3])
            self.assertEqual(result["second_role_counts"], {"6": 1, "8": 1, "9": 1})
            self.assertFalse(result["truncated"])
            self.assertFalse(result["full_dialog_or_controls_verified"])
            self.assertEqual(result["editor_actions"], 0)
            self.assertTrue((Path(directory)/"shallow-grandchildren-return.json").exists())
            for suffix in ("reference-return", "role-start", "role-return",
                           "count-start", "count-return"):
                self.assertTrue((Path(directory)/("shallow-child-one-"+suffix+".json")).exists())

    def test_wide_accessibility_subtree_is_not_exhaustively_traversed(self):
        class Node:
            def get_child_count(self): return 10000
            def get_child_at_index(self, index):
                if index > 7: raise AssertionError("unbounded top-level read")
                return Child()
        class Child:
            def get_role(self): return 10
            def get_child_count(self): return 10000
            def get_child_at_index(self, index):
                if index > 15: raise AssertionError("unbounded nested read")
                return Grandchild()
        class Grandchild:
            def get_role(self): return 77
        class Bridge:
            def call(self, fn, *args): return fn(*args)
        with tempfile.TemporaryDirectory() as directory:
            result = diagnostic.read_shallow_roles(Bridge(), Node(), Path(directory))
            self.assertEqual(result["first_roles"], [10]*8)
            self.assertEqual(result["second_role_counts"], {"77": 16})
            self.assertTrue(result["truncated"])
            self.assertEqual(result["second_examined"], 16)

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
        self.assertIn("14134725424", raw)
        self.assertIn("7a8cb5445a4f408eeaf5d2643adae904d2d9f66fb119bfc8a0bf02fa34bba192", raw)
        self.assertIn("487323a3b7021c6635f61adab2a3cad2811bf294dcbffc4fb8f92af9eae84e92", raw)
        self.assertIn("run-id: 43645341171", raw)
        self.assertIn("ORIGINAL_ATSPI_CLIENT_BLOB", raw)
        self.assertIn("ORIGINAL_GUI_PROBE_BLOB", raw)
        self.assertNotIn("fetch-depth: 0", raw)
        self.assertIn("timeout -k 5s 65s", raw)
        self.assertIn('at-spi2-core libglib2.0-bin libatk-adaptor', raw)
        self.assertIn('dpkg-query -W libatk-adaptor libatk-bridge2.0-0t64', raw)
        self.assertIn('dbus-run-session', raw)
        self.assertIn("on:\n  push:\n", raw)
        self.assertIn("if: always()", raw)
        for forbidden in ("tauri build", "cargo build", "gh release", "gh workflow run"):
            self.assertNotIn(forbidden, raw)


if __name__ == "__main__":
    unittest.main()
