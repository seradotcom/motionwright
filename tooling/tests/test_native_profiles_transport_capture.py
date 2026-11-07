#!/usr/bin/env python3
"""G0 Linux captures of the exact binary transport; never Native or a grant."""
from pathlib import Path
import hashlib
import io
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_profiles_custody as custody


@unittest.skipUnless(os.name == "posix" and os.open in os.supports_dir_fd,
                     "requires real Linux openat custody")
class OfficialRuntimeTransportCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hero-transport-g0-")
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("transport-fixture.bin", bytes(9 * custody.MIB))
        self.binary = output.getvalue()
        self.expected = custody.route.descriptor(self.binary)

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self, label, relative, name, payload_max):
        selected = self.root / (name + "-input")
        selected.mkdir(mode=0o700)
        path = selected / relative
        path.parent.mkdir(mode=0o700, parents=True)
        path.write_bytes(self.binary)
        destination = self.root / (name + ".tar")
        actual, manifest = custody.capture_tar(
            destination, {label: selected}, {label: lambda parts: True},
            current={"source_sha": "0" * 40, "semwright_sha": "1" * 40,
                     "run_id": "1", "run_attempt": "1"},
            scope="shared-preparation-only", payload_max=payload_max, files_max=16,
            claims={"G0_fixture": True, "Native_calls": 0})
        self.assertEqual(path.read_bytes(), self.binary)
        return destination, actual, manifest

    def test_exact_runtime_transport_over_stream_cap_is_fully_retained(self):
        self.assertGreater(len(self.binary), custody.route.LIMITS["stream_bytes"])
        destination, actual, manifest = self.capture(
            "runtime", "intake/official-zip.stdout", "transport", 240 * custody.MIB)
        self.assertTrue(manifest["selected_originals_complete"])
        row, = manifest["members"]
        self.assertEqual(row["status"], "INCLUDED")
        self.assertEqual({key: row[key] for key in ("bytes", "sha256")}, self.expected)
        self.assertEqual(actual, custody.file_digest(destination, 256 * custody.MIB))
        with tarfile.open(destination, "r|") as archive:
            member = next(iter(archive))
            self.assertEqual(member.name, "runtime/intake/official-zip.stdout")
            observed = archive.extractfile(member).read()
        self.assertEqual(hashlib.sha256(observed).hexdigest(), self.expected["sha256"])

    def test_same_bytes_are_not_admitted_as_generic_log_or_another_root(self):
        for label, relative, name in (
            ("runtime", "intake/other.stdout", "log"),
            ("source", "intake/official-zip.stdout", "wrong-root"),
        ):
            with self.subTest(label=label, relative=relative):
                _, _, manifest = self.capture(label, relative, name, 240 * custody.MIB)
                self.assertFalse(manifest["selected_originals_complete"])
                row, = manifest["members"]
                self.assertEqual(row["status"], "CAPTURE_FAILED")
                self.assertEqual(row["error_type"], "ProfileRejected")
                self.assertTrue(row["original_retained_in_place"])
                self.assertEqual(manifest["selected_payload_bytes"], 0)

    def test_original_payload_cap_still_rejects_the_binary(self):
        _, _, manifest = self.capture(
            "runtime", "intake/official-zip.stdout", "payload-cap", len(self.binary) - 1)
        self.assertFalse(manifest["selected_originals_complete"])
        row, = manifest["members"]
        self.assertEqual(row["status"], "CAPTURE_FAILED")
        self.assertEqual(manifest["selected_payload_bytes"], 0)
        self.assertEqual(custody.FILE_MAX, 256 * custody.MIB)
        self.assertEqual(custody.route.LIMITS["stream_bytes"], 8 * custody.MIB)


if __name__ == "__main__":
    unittest.main()
