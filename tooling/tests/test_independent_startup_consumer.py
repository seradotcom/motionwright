"""Boundary tests for the frozen external bootstrap watchdog source.

These tests are source-only. They never launch a child or claim an installed
session outcome. A pinned source hash is not a Broker/user authorization.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest import mock


TOOLING = Path(__file__).resolve().parents[1]
CONSUMER = TOOLING / "independent_startup.py"
OUTER = TOOLING / "startup_capture_outer.py"
REVIEWED_SHA = "a47c5e6920ff9748dfe708bac554be1f6150414b8b186277ba1b5e366265dc37"
LAUNCHER = TOOLING / "startup_capture_launcher.py"
LAUNCHER_SHA = "57e0ffbd7ec02bd2dc0fcb590e7ea286074e661f3c09f37f871cdcb536b9906b"


def read_module():
    spec = importlib.util.spec_from_file_location("motionwright_independent_bootstrap", CONSUMER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def static_constant(source: Path, constant: str):
    nodes = ast.parse(source.read_text(encoding="utf-8")).body
    values = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == constant for target in node.targets):
                values.append(ast.literal_eval(node.value))
    if len(values) != 1:
        raise AssertionError(f"{constant} must be a single literal source constant")
    return values[0]


class FrozenExternalWatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bootstrap = read_module()

    def test_exact_reviewed_outer_is_really_the_source_on_disk(self):
        digest = hashlib.sha256(OUTER.read_bytes()).hexdigest()
        self.assertEqual(digest, REVIEWED_SHA)
        self.assertEqual(self.bootstrap.REVIEWED_OUTER_SOURCES, frozenset({digest}))

    def test_launcher_is_frozen_separately_not_selected_by_consumer(self):
        self.assertEqual(hashlib.sha256(LAUNCHER.read_bytes()).hexdigest(), LAUNCHER_SHA)
        self.assertNotIn(LAUNCHER_SHA, self.bootstrap.REVIEWED_OUTER_SOURCES)
        self.assertEqual(len(self.bootstrap.REVIEWED_OUTER_SOURCES), 1)

    def test_five_actual_outer_channels_match_consumer(self):
        expected = ("phase", "observer_stdout", "observer_stderr",
                    "capture_result", "observer_subtree_status")
        self.assertEqual(self.bootstrap.CAPTURE_CHANNEL_NAMES, expected)
        self.assertEqual(tuple(static_constant(OUTER, "CHANNEL_NAMES")), expected)
        self.assertEqual(static_constant(OUTER, "CAPTURE_SCHEMA"), self.bootstrap.CAPTURE_SCHEMA)

    def test_observer_subtree_core_is_an_independent_source_not_a_sixth_channel(self):
        core_sha = static_constant(OUTER, "CORE_SHA256")
        self.assertEqual(self.bootstrap.SUBTREE_CORE_SHA, core_sha)
        self.assertNotIn("observer_subtree_core", self.bootstrap.CAPTURE_CHANNEL_NAMES)

    def test_empty_external_parent_rejects_without_fs_or_process_access(self):
        b = self.bootstrap
        with mock.patch.object(b, "checked_file", side_effect=AssertionError("FILE_READ_FORBIDDEN")) as checked:
            with mock.patch.object(b.os, "getppid", side_effect=AssertionError("PID_READ_FORBIDDEN")) as pid:
                with self.assertRaisesRegex(b.Refused, "MISSING_REVIEWED_OUTER_WATCHDOG"):
                    b.verify_outer({}, 32 * b.NS)
                checked.assert_not_called()
                pid.assert_not_called()

    def test_foreign_outer_digest_rejects_before_file_or_pid_lookup(self):
        b = self.bootstrap
        spec = {"external_parent": {"source": {"sha256": "e"*64}}}
        with mock.patch.object(b, "checked_file", side_effect=AssertionError("FILE_READ_FORBIDDEN")) as checked:
            with mock.patch.object(b.os, "getppid", side_effect=AssertionError("PID_READ_FORBIDDEN")) as pid:
                with self.assertRaisesRegex(b.Refused, "OUTER_WATCHDOG_SOURCE_NOT_REVIEWED"):
                    b.verify_outer(spec, 32*b.NS)
                checked.assert_not_called()
                pid.assert_not_called()

    def test_reviewed_source_cannot_override_missing_admission_status(self):
        b = self.bootstrap
        spec = {"external_parent": {
            "source": {"sha256": REVIEWED_SHA},
            "status": "NOT_ADMITTED",
        }}
        with mock.patch.object(b, "checked_file", side_effect=AssertionError("FILE_READ_FORBIDDEN")) as checked:
            with self.assertRaisesRegex(b.Refused, "OUTER_ADMISSION_REQUIRED"):
                b.verify_outer(spec, 32*b.NS)
            checked.assert_not_called()

    def test_reviewed_source_cannot_extend_cleanup_deadline(self):
        b = self.bootstrap
        spec = {"external_parent": {
            "source": {"sha256": REVIEWED_SHA},
            "status": "A1_EXTERNAL_BOOTSTRAP_WATCHDOG_ADMITTED",
            "deadline_mono_ns": 40*b.NS,
        }}
        with mock.patch.object(b, "checked_file", side_effect=AssertionError("FILE_READ_FORBIDDEN")) as checked:
            with self.assertRaisesRegex(b.Refused, "OUTER_DEADLINE_MUST_EQUAL_FIXED_C"):
                b.verify_outer(spec, 32*b.NS)
            checked.assert_not_called()

    def test_retained_t0_fixed_24_and_32_second_caps(self):
        b = self.bootstrap
        t0 = 5_000_000_000
        spec = {"operation_id": "93b0705e-6ad1-43af-84d9-36392213a109",
                "start_mono_ns": t0, "parent": "."}
        with mock.patch.object(b.time, "monotonic_ns", side_effect=AssertionError("AMBIENT_CLOCK_FORBIDDEN")):
            bootstrap = b.Bootstrap(spec)
        self.assertEqual(bootstrap.deadline, t0 + 24*b.NS)
        self.assertEqual(bootstrap.cleanup_cap, t0 + 32*b.NS)


if __name__ == "__main__":
    unittest.main()
