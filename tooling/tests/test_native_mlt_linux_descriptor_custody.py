"""Linux OS descriptor custody only; no native renderer or module attestation."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native_mlt_g2_observation as g2


@unittest.skipUnless(sys.platform == "linux","requires the real Linux procfs descriptor bridge")
class LinuxOriginalDescriptorCustodyTests(unittest.TestCase):
    def test_original_writable_descriptor_survives_unlink_and_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            stage = root/"partial.mp4"
            master = root/"published.mp4"
            evidence = root/"evidence"
            evidence.mkdir()
            original = bytes(range(256))+b"\r\n\x1a-original-custody-fixture"
            stage.write_bytes(original)
            with stage.open("r+b") as writer:
                proc = Path("/proc")/str(os.getpid())
                ticks = g2.process_identity(os.getpid())[1]
                descriptor = g2.namespace_path(proc,stage.as_posix())
                opened = os.fstat(descriptor)
                held = g2.NamespaceMaster((os.getpid(),ticks),descriptor,opened,{
                    "output_path":stage.as_posix(),"live_module":{"test_double":True},
                })
                try:
                    writable = g2.matching_write_descriptors(proc,opened)
                    self.assertIn(writer.fileno(),writable)
                    self.assertNotIn(descriptor,writable)
                    master.write_bytes(stage.read_bytes())
                    stage.unlink()
                    stage.write_bytes(b"foreign pathname replacement")
                    position = writer.tell()
                    with mock.patch.object(g2,"namespace_path") as reopen:
                        held.retain(evidence)
                        raw,proof = g2.read_held_output(descriptor,opened)
                    reopen.assert_not_called()
                    self.assertEqual(writer.tell(),position)
                    self.assertEqual(raw,original)
                    self.assertEqual(raw,master.read_bytes())
                    self.assertEqual(proof["file_identity"][1],os.fstat(writer.fileno()).st_ino)
                    self.assertNotEqual(proof["file_identity"][1],stage.stat().st_ino)
                    self.assertEqual(proof["links_at_read_time"],0)
                    self.assertEqual(held.private_snapshot["sha256"],g2.sha(original))
                finally:
                    held.close()


if __name__ == "__main__":
    unittest.main()
