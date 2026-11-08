"""Real filesystem checks for independently retained review originals."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ReviewOriginalIoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='review-original-io-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve(strict=True) / 'original.bin'

    def test_exclusive_write_retains_exact_bytes_and_refuses_overwrite(self):
        import review_original_io as originals
        raw = b'original binary\x00\xff\n' * 100
        originals.new_bytes(self.path, raw)
        self.assertEqual(originals.read_bytes(self.path, len(raw)), raw)
        with self.assertRaises(FileExistsError): originals.new_bytes(self.path, b'other')
        self.assertEqual(self.path.read_bytes(), raw)

    def test_directory_is_not_an_original(self):
        import review_original_io as originals
        self.path.mkdir()
        from mlt_official_runtime_intake import IntakeError
        with self.assertRaises((ValueError, OSError, IntakeError)):
            originals.read_bytes(self.path, 100)

    @unittest.skipUnless(os.name == 'nt', 'Windows mandatory sharing and link custody')
    def test_linked_or_live_writer_original_is_rejected(self):
        import review_original_io as originals
        self.path.write_bytes(b'inert original')
        alias = self.path.with_suffix('.alias')
        os.link(self.path, alias)
        with self.assertRaises(ValueError): originals.read_bytes(self.path, 100)
        alias.unlink()
        with self.path.open('r+b'):
            with self.assertRaises((ValueError, OSError)):
                originals.read_bytes(self.path, 100)
        self.assertEqual(originals.read_bytes(self.path, 100), b'inert original')


if __name__ == '__main__': unittest.main()
