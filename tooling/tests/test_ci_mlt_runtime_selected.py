"""Negative and positive controls for the source-bound MLT runtime G1 selector."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_mlt_runtime_selected as selected


class MltPlatformSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parent /
                      "test_native_mlt_runtime_admission.py").read_bytes()

    @staticmethod
    def replace(blob: bytes, old: bytes, new: bytes) -> bytes:
        assert blob.count(old) == 1, (old, blob.count(old))
        return blob.replace(old, new)

    def test_thirteen_original_method_names_are_explicitly_reviewed(self):
        self.assertEqual(len(selected.EXPECTED), 13)
        self.assertIn(selected.WINDOWS_CASE, selected.EXPECTED)

    def test_linux_admits_exactly_twelve_explicit_nonwindows_cases(self):
        methods = selected.selected_names(self.source, "linux")
        self.assertEqual(len(methods), 12)
        self.assertEqual(set(methods), selected.EXPECTED - {selected.WINDOWS_CASE})
        self.assertNotIn(selected.WINDOWS_CASE, methods)

    def test_windows_admits_exact_one_os_negative_case(self):
        self.assertEqual(selected.selected_names(self.source, "windows"),
                         (selected.WINDOWS_CASE,))

    def test_unreviewed_platform_refused(self):
        with self.assertRaisesRegex(selected.ContractError, "unsupported platform"):
            selected.selected_names(self.source, "darwin")

    def test_unreviewed_extra_method_refused(self):
        source = self.source + b"\nclass Other(unittest.TestCase):\n    def test_unreviewed(self): pass\n"
        with self.assertRaisesRegex(selected.ContractError, "inventory diverges"):
            selected.selected_names(source, "linux")

    def test_renamed_member_refused(self):
        name = b"def test_known_original_source_sha_is_fixed_not_discovered_dynamically"
        altered = self.replace(self.source, name, b"def test_WEAK_ORIGINAL_SOURCE_HASH")
        with self.assertRaisesRegex(selected.ContractError, "inventory diverges"):
            selected.selected_names(altered, "linux")

    def test_missing_method_refused(self):
        name = b"def test_nonexistent_source_cannot_be_read_or_written"
        altered = self.replace(self.source, name, b"def disabled_nonexistent_source_cannot_be_read_or_written")
        with self.assertRaisesRegex(selected.ContractError, "inventory diverges"):
            selected.selected_names(altered, "linux")

    def test_duplicate_member_refused(self):
        old = b"def test_nonexistent_source_cannot_be_read_or_written"
        source = self.source + b"\nclass NativeModuleAdmissionTests(unittest.TestCase):\n    def test_nonexistent_source_cannot_be_read_or_written(self): pass\n"
        with self.assertRaisesRegex(selected.ContractError, "duplicated"):
            selected.selected_names(source, "linux")

    def test_dynamic_unittest_loader_refused(self):
        source = self.source + b"\ndef load_tests(*args):return unittest.TestSuite()\n"
        with self.assertRaisesRegex(selected.ContractError, "dynamic unittest loader"):
            selected.selected_names(source, "linux")

    def test_loss_of_unittest_base_refused(self):
        source = self.replace(self.source,
                              b"class NativeModuleAdmissionTests(unittest.TestCase):",
                              b"class NativeModuleAdmissionTests(object):")
        with self.assertRaisesRegex(selected.ContractError, "unittest.TestCase"):
            selected.selected_names(source, "windows")

    def test_removing_explicit_linux_skip_guard_refused(self):
        source = self.replace(self.source,
                              b'if sys.platform == "linux":',
                              b'if sys.platform == "darwin":')
        with self.assertRaisesRegex(selected.ContractError, "inapplicability"):
            selected.selected_names(source, "windows")

    def test_non_utf8_source_refused(self):
        with self.assertRaisesRegex(selected.ContractError, "unparseable"):
            selected.selected_names(self.source + b"\xff", "windows")

    def test_verbose_run_requires_exact_methods_and_terminal_ok(self):
        methods = selected.selected_names(self.source, "windows")
        only = methods[0]
        payload = (f"{only} (tooling.tests.test_native_mlt_runtime_admission."
                   f"NativeModuleAdmissionTests.{only}) ... ok\n"
                   "\n----------------------------------------------------------------------\n"
                   "Ran 1 test in 0.001s\n\nOK\n").encode()
        selected.verify_verbose(payload, methods)
        with self.assertRaisesRegex(selected.ContractError, "verbose"):
            selected.verify_verbose(payload.replace(b" ... ok", b" ... skipped"), methods)
        with self.assertRaisesRegex(selected.ContractError, "completed count"):
            selected.verify_verbose(payload.replace(b"Ran 1 test", b"Ran 0 tests"), methods)
        with self.assertRaisesRegex(selected.ContractError, "never accepted"):
            selected.verify_verbose(payload.replace(b"\nOK\n", b"\nOK (skipped=1)\n"), methods)

    def test_source_bound_byte_hash_check_positive_and_corruption_negative(self):
        raw = b"MLT-immutable-source-unit-fixture"
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "original.c"
            p.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            self.assertEqual(selected.checked_c_source(p, digest, len(raw)), digest)
            with self.assertRaisesRegex(selected.ContractError, "size mismatch"):
                selected.checked_c_source(p, digest, len(raw) + 1)
            with self.assertRaisesRegex(selected.ContractError, "bytes differ"):
                selected.checked_c_source(p, "0" * 64, len(raw))


if __name__ == "__main__":
    unittest.main()
