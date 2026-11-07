"""Run the actual private Rust root preparation; no toolchain or Native work."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-manim-e2e.yml"
PREPARE = "Prepare isolated Rust toolchain root"
ADMIT = "Admit toolchain within the original editable deadline"
ACTION = "dtolnay/rust-toolchain@02cb101ec7c40f2c49e1d9714d64511d8e1b74de"


def step(text, name):
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if line.strip() == "- name: " + name]
    if len(hits) != 1:
        raise AssertionError("Expected one workflow step: " + name)
    start = hits[0]
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith(" " * indent + "- ")), len(lines))
    return "\n".join(lines[start:end]) + "\n"


def shell(block):
    return textwrap.dedent(block.split("run: |\n", 1)[1]).rstrip() + "\n"


class PrivateRustupRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "posix":
            raise unittest.SkipTest("The Linux runner's private directory mode requires POSIX")
        cls.bash = shutil.which("bash")
        if cls.bash is None:
            raise AssertionError("Bash is required to execute the actual workflow preparation")
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.preparation = step(cls.text, PREPARE)
        cls.script = shell(cls.preparation)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-manim-rustup-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.output = self.root / "github-env"
        self.output.write_bytes(b"EXISTING=preserved\n")
        self.owned = self.root / "manim-native-rustup-12345-1"
        self.runner = self.root / "runner-rustup"
        self.cargo = self.root / "cargo-cache"
        for directory in (self.runner, self.cargo):
            directory.mkdir()
            (directory / "retained").write_bytes(b"existing runner/cache state")
        self.environment = {
            **os.environ, "RUNNER_TEMP": str(self.root), "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_ENV": str(self.output),
            "RUSTUP_HOME": str(self.runner), "CARGO_HOME": str(self.cargo),
        }

    def execute(self, **changes):
        return subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c", self.script],
            cwd=self.root, env={**self.environment, **changes},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)

    def assert_retained_state(self):
        for directory in (self.runner, self.cargo):
            self.assertEqual((directory / "retained").read_bytes(),
                             b"existing runner/cache state")
            self.assertEqual([p.name for p in directory.iterdir()], ["retained"])

    def test_real_shell_creates_private_root_and_exports_exact_owned_path(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.owned.stat().st_mode), 0o700)
        self.assertEqual(list(self.owned.iterdir()), [])
        self.assertEqual(self.output.read_bytes(), (
            "EXISTING=preserved\nRUSTUP_HOME=" + str(self.owned) + "\n").encode())
        self.assertEqual(result.stdout, "")
        self.assert_retained_state()

    def test_existing_directory_file_and_symlink_are_never_overwritten_or_exported(self):
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    self.owned.mkdir(mode=0o755)
                    (self.owned / "original").write_bytes(b"keep")
                elif kind == "file":
                    self.owned.write_bytes(b"keep")
                else:
                    self.owned.symlink_to(self.runner, target_is_directory=True)
                before = self.owned.lstat()
                result = self.execute()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output.read_bytes(), b"EXISTING=preserved\n")
                after = self.owned.lstat()
                self.assertEqual((before.st_ino, before.st_mode), (after.st_ino, after.st_mode))
                if kind == "directory":
                    self.assertEqual((self.owned / "original").read_bytes(), b"keep")
                    (self.owned / "original").unlink()
                    self.owned.rmdir()
                else:
                    if kind == "file":
                        self.assertEqual(self.owned.read_bytes(), b"keep")
                    else:
                        self.assertEqual(self.owned.resolve(), self.runner)
                    self.owned.unlink()
                self.assert_retained_state()

    def test_empty_runner_temp_fails_before_creation_or_export(self):
        result = self.execute(RUNNER_TEMP="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.owned.exists())
        self.assertEqual(self.output.read_bytes(), b"EXISTING=preserved\n")
        self.assert_retained_state()

    def test_preparation_reuses_blender_pattern_before_existing_deadline_and_pinned_action(self):
        blender = (ROOT / ".github/workflows/native-blender-e2e.yml").read_text(encoding="utf-8")
        self.assertEqual(self.script, shell(step(blender, PREPARE)).replace(
            "blender-lit-rustup-", "manim-native-rustup-"))
        native = self.text.split("  native-manim-roundtrip:\n", 1)[1]
        self.assertLess(native.index(PREPARE), native.index(ADMIT))
        self.assertLess(native.index(ADMIT), native.index(ACTION))
        self.assertLess(native.index("Start one editable outer clock before preparation"),
                        native.index(PREPARE))
        self.assertIn("timeout-minutes: 1\n", self.preparation)
        admission = step(self.text, ADMIT)
        self.assertIn("steps.editable_outer_origin.outcome == 'success'", admission)
        self.assertIn("native_manim_editable_outer.py admit-action --label toolchain", admission)
        action = native.split("- uses: " + ACTION + "\n", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("steps.editable_toolchain_budget.outputs.minutes", action)
        self.assertIn("toolchain: 1.98.1\n", action)
        self.assertNotIn("CARGO_HOME", self.preparation)
        self.assertNotIn("CARGO_TARGET_DIR", self.preparation)

    def test_native_action_installs_all_pinned_components_before_sdk_build(self):
        # Both pinned workspaces require rustfmt and clippy. Omitting rustfmt
        # defers its installation to cargo in the SDK directory, where the
        # retained original failed on a cargo-fmt component conflict.
        required = tomllib.loads((ROOT / "rust-toolchain.toml").read_text(
            encoding="utf-8"))["toolchain"]
        self.assertEqual(required["channel"], "1.98.1")
        self.assertEqual(required["profile"], "minimal")
        self.assertEqual(set(required["components"]), {"rustfmt", "clippy"})
        native = self.text.split("  native-manim-roundtrip:\n", 1)[1]
        action = native.split("- uses: " + ACTION + "\n", 1)[1].split("\n      - ", 1)[0]
        rows = [line.strip().split(":", 1)[1].strip() for line in action.splitlines()
                if line.strip().startswith("components:")]
        self.assertEqual(len(rows), 1)
        self.assertEqual({name.strip() for name in rows[0].split(",")},
                         set(required["components"]))
        self.assertLess(native.index("- uses: " + ACTION),
                        native.index("- name: Build exact Semwright Broker and sandbox"))


if __name__ == "__main__":
    unittest.main()
