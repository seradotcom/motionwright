"""Focused Manim source-only binary-custody regressions (not native acceptance).

These controls operate on real private files. They never claim to launch
Semwright, render Manim or certify a release gate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "native_manim_e2e.py"
SPEC = importlib.util.spec_from_file_location("native_manim_e2e_stage_original", TOOL)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class OriginalManimStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.binaries = self.root / "owned-bin"
        self.source.mkdir()
        self.binaries.mkdir()
        self.driver_source = self.source / "motionwright-manim-community-driver"
        self.runner_source = self.source / "motionwright-manim-runner"
        self.driver_source.write_bytes(b"original-driver-verified-input-01")
        self.runner_source.write_bytes(b"original-runner-verified-input-02")

    def stage(self, reuse: bool):
        return module.stage_phase_binaries(
            self.binaries, reuse,
            driver_source=self.driver_source,
            runner_source=self.runner_source,
        )

    def test_first_stage_preserves_source_hashes(self):
        driver, runner = self.stage(False)
        self.assertEqual(driver.read_bytes(), self.driver_source.read_bytes())
        self.assertEqual(runner.read_bytes(), self.runner_source.read_bytes())
        self.assertEqual(set(p.name for p in self.binaries.iterdir()),
                         {driver.name, runner.name})

    def test_reuse_is_read_only_and_keeps_exact_original_bytes(self):
        driver, runner = self.stage(False)
        before = ((driver.stat().st_mtime_ns, driver.read_bytes()),
                  (runner.stat().st_mtime_ns, runner.read_bytes()))
        self.assertEqual(self.stage(True), (driver, runner))
        after = ((driver.stat().st_mtime_ns, driver.read_bytes()),
                 (runner.stat().st_mtime_ns, runner.read_bytes()))
        self.assertEqual(before, after)

    def test_missing_original_refuses_reuse_without_replacement(self):
        driver, runner = self.stage(False)
        runner.unlink()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.stage(True)
        self.assertTrue(driver.is_file())
        self.assertFalse(runner.exists())

    def test_tampered_original_refuses_reuse_without_replacement(self):
        driver, runner = self.stage(False)
        driver.write_bytes(b"attacker-tampered-original")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.stage(True)
        self.assertEqual(driver.read_bytes(), b"attacker-tampered-original")
        self.assertEqual(runner.read_bytes(), self.runner_source.read_bytes())

    def test_reuse_rejects_new_source_binary_without_replacing_original(self):
        driver, runner = self.stage(False)
        self.runner_source.write_bytes(b"new-runner-source-not-the-original")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.stage(True)
        self.assertEqual(runner.read_bytes(), b"original-runner-verified-input-02")
        self.assertEqual(driver.read_bytes(), self.driver_source.read_bytes())

    def test_first_stage_refuses_preexisting_target(self):
        original_driver = self.binaries / "motionwright-manim-community-driver"
        original_driver.write_bytes(b"untrusted-occupied-target")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.stage(False)
        self.assertEqual(original_driver.read_bytes(), b"untrusted-occupied-target")
        self.assertFalse((self.binaries / "motionwright-manim-runner").exists())


if __name__ == "__main__":
    unittest.main()
