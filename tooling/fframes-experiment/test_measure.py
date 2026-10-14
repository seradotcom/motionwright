#!/usr/bin/env python3
"""Safety/validity tests for the optional pinned fframes evaluation protocol."""
from __future__ import annotations
import hashlib
from pathlib import Path
import tempfile
import unittest
from measure import PIN,REPO,LICENSE,png_metadata,expect,OUT,CHECKOUT

class FframesProtocol(unittest.TestCase):
    def test_public_git_sha_and_zero_automatic_release_admission(self):
        self.assertEqual(len(PIN),40)
        self.assertTrue(all(char in "0123456789abcdef" for char in PIN))
        self.assertEqual(REPO,"https://github.com/dmtrKovalenko/fframes.git")
        self.assertEqual(LICENSE,"MIT")
        self.assertNotEqual(OUT,CHECKOUT)
    def test_actual_binary_png_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'test.png'
            import struct
            data=b"\x89PNG\r\n\x1a\n"+b"\x00\x00\x00\x0dIHDR"+struct.pack(">II",320,180)
            p.write_bytes(data)
            inspected=png_metadata(p)
            self.assertEqual((inspected["width"],inspected["height"]),(320,180))
            self.assertEqual(inspected["sha256"],hashlib.sha256(data).hexdigest())
            p.write_bytes(b"Fake PNG data that is not a real frame")
            with self.assertRaises(AssertionError):png_metadata(p)
    def test_unbounded_source_preview_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"too_big.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n"+b"\x00"*20)
            with self.assertRaises(AssertionError):png_metadata(p)

if __name__=="__main__":unittest.main()
