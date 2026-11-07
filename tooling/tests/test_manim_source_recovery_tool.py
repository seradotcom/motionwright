"""Real filesystem controls for the public source-recovery code artifact."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "retain_manim_source_recovery_tool.py"
SPEC = importlib.util.spec_from_file_location("manim_source_recovery_tool", SOURCE)
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


@unittest.skipUnless(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "Unix file identity controls")
class PublicRecoveryToolCustodyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mw-recovery-code-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_group_writable_originals_keep_their_bytes_and_modes(self):
        for mode in (0o775, 0o664):
            with self.subTest(mode=oct(mode)):
                source = self.root / ("original-" + oct(mode))
                original = b"public compiled source fixture"
                source.write_bytes(original)
                source.chmod(mode)
                before = source.stat()
                self.assertEqual(TOOL.regular(source, 1024, private_mode=False), original)
                after = source.stat()
                self.assertEqual((before.st_ino, before.st_mode, before.st_mtime_ns, before.st_ctime_ns),
                                 (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns))
                self.assertEqual(stat.S_IMODE(after.st_mode), mode)

    def test_private_copy_check_rejects_group_writable_modes(self):
        path = self.root / "untrusted-copy"
        path.write_bytes(b"copy")
        for mode in (0o775, 0o664):
            path.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(ValueError):
                TOOL.regular(path, 1024, single_link=True)

    def test_symlink_and_fifo_originals_are_rejected_without_waiting(self):
        original = self.root / "original"
        original.write_bytes(b"public")
        symlink = self.root / "symlink"
        symlink.symlink_to(original)
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        for path in (symlink, fifo):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                TOOL.regular(path, 1024, private_mode=False)

    def test_cargo_hardlink_copies_are_private_and_collisions_preserve_bytes(self):
        original = self.root / "cargo-original"
        raw = b"\x7fELFpublic compiled fixture"
        original.write_bytes(raw)
        original.chmod(0o775)
        os.link(original, self.root / "cargo-dependency-hardlink")
        self.assertEqual(original.stat().st_nlink, 2)
        observed = TOOL.regular(original, 1024, private_mode=False)
        for mode in (0o500, 0o600):
            with self.subTest(mode=oct(mode)):
                copied = self.root / ("private-" + oct(mode))
                TOOL.exclusive(copied, observed, mode)
                self.assertEqual(copied.stat().st_nlink, 1)
                self.assertEqual(stat.S_IMODE(copied.stat().st_mode), mode)
                self.assertEqual(TOOL.regular(copied, 1024, single_link=True), raw)
                with self.assertRaises(FileExistsError):
                    TOOL.exclusive(copied, b"replacement", mode)
                self.assertEqual(copied.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o775)
        self.assertEqual(original.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
