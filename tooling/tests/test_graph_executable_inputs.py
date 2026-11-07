"""Focal filesystem controls for pre-R74 copies; no SDK/Native invocation."""
import copy
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_graph_executable_inputs as inputs
import native_graph_browser_owner as owner


@unittest.skipUnless(sys.platform == "linux", "Graph custody requires Linux")
class ExecutableInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repo = Path(inputs.__file__).resolve().parents[1]
        self.source = {"repository_root": str(self.repo), "sha": "a" * 40, "sdk_sha": "b" * 40,
                       "run_id": "12345", "run_attempt": 1}
        self.binaries = {}
        # Copy a genuine existing ELF without running it. The original system
        # file is never modified; writable input modes belong only to fixtures.
        executable = Path(shutil.which("true")).resolve(strict=True)
        raw = executable.read_bytes()
        self.assertTrue(raw.startswith(b"\x7fELF"))
        for role in inputs.ROLES:
            path = self.root / ("original-" + role)
            path.write_bytes(raw); path.chmod(0o775)
            self.binaries[role] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
        self.directory = self.root / "private-inputs"
        self.intents = []

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self):
        def retain(value):
            self.assertFalse(self.directory.exists())
            self.intents.append(copy.deepcopy(value))
        return inputs.prepare(self.directory, self.binaries, self.source, retain)

    def test_exact_private_copies_preserve_original_bytes_and_modes(self):
        before = {role: inputs.record_metadata(inputs.executable_metadata(Path(ref["path"]).stat()))
                  for role, ref in self.binaries.items()}
        record = self.prepare()
        self.assertEqual(len(self.intents), 1)
        self.assertEqual(record["copy_bytes"], sum(row["size_bytes"] for row in self.binaries.values()))
        self.assertEqual(record["limits"], inputs.LIMITS)
        for role, row in record["inputs"].items():
            source, prepared = Path(row["source"]["path"]), Path(row["prepared"]["path"])
            self.assertEqual(source.read_bytes(), prepared.read_bytes())
            self.assertEqual(before[role], inputs.record_metadata(inputs.executable_metadata(source.stat())))
            self.assertEqual(stat.S_IMODE(prepared.stat().st_mode), 0o500)
            self.assertEqual(prepared.stat().st_uid, os.getuid())
            self.assertEqual(prepared.stat().st_nlink, 1)
            self.assertNotEqual(source.stat().st_ino, prepared.stat().st_ino)
            self.assertIsInstance(row["prepared_metadata"]["mtime_ns"], str)
        self.assertEqual(len(inputs.validate_live(record, self.binaries, self.source, self.directory)), 4)

    def test_existing_preparation_is_never_retried(self):
        record = self.prepare()
        original = (self.directory / "python3").read_bytes()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(len(self.intents), 1)
        self.assertEqual((self.directory / "python3").read_bytes(), original)
        inputs.validate_live(record, self.binaries, self.source, self.directory)

    def test_copy_mode_change_is_denied_before_host(self):
        record = self.prepare()
        (self.directory / "python3").chmod(0o755)
        with self.assertRaises(ValueError):
            inputs.validate_live(record, self.binaries, self.source, self.directory)

    def test_source_mode_change_is_denied_without_chmod_repair(self):
        record = self.prepare()
        path = Path(self.binaries["native_provider"]["path"])
        path.chmod(0o777)
        with self.assertRaises(ValueError):
            inputs.validate_live(record, self.binaries, self.source, self.directory)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o777)

    def test_same_size_different_copy_bytes_are_denied(self):
        record = self.prepare()
        path = self.directory / "motionwright-native-provider"
        path.chmod(0o600)
        raw = path.read_bytes(); path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1])); path.chmod(0o500)
        # Even if someone forges the newly observed inode timestamps, the
        # authenticated original digest remains authoritative.
        record["inputs"]["native_provider"]["prepared_metadata"] = inputs.record_metadata(inputs.executable_metadata(path.stat()))
        with self.assertRaises(ValueError):
            inputs.validate_live(record, self.binaries, self.source, self.directory)

    def test_added_hardlink_is_denied(self):
        record = self.prepare()
        os.link(self.directory / "ffmpeg", self.root / "aliased-copy")
        with self.assertRaises(ValueError):
            inputs.validate_live(record, self.binaries, self.source, self.directory)

    def test_foreign_claimed_owner_is_denied(self):
        record = self.prepare()
        record["inputs"]["python3"]["prepared_metadata"]["uid"] += 1
        with self.assertRaises(ValueError):
            inputs.validate_contract(record, self.binaries, self.source, self.directory)

    def test_original_path_cannot_substitute_prepared_copy(self):
        record = self.prepare()
        record["inputs"]["python3"]["prepared"] = dict(self.binaries["python3"])
        with self.assertRaises(ValueError):
            inputs.validate_contract(record, self.binaries, self.source, self.directory)

    def test_resource_overflow_precedes_intent_or_destination(self):
        for row in self.binaries.values():
            row["size_bytes"] = inputs.PER_FILE_BYTES
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.intents, [])

    def test_staging_contract_cannot_raise_other_caps_or_drop_reserved_bytes(self):
        record = self.prepare()
        for field, value in (("case_hard_file_bytes", inputs.PER_FILE_BYTES),
                             ("other_child_file_bytes", inputs.PER_FILE_BYTES),
                             ("driver_file_bytes", inputs.PER_FILE_BYTES),
                             ("staging_total_bytes", 1), ("simultaneous_owner_limit", 2),
                             ("charged_to_producer_allocation", False)):
            with self.subTest(field=field):
                mutated = copy.deepcopy(record)
                mutated["daemon_staging"][field] = value
                with self.assertRaisesRegex(ValueError, "Graph daemon staging contract"):
                    inputs.validate_contract(mutated, self.binaries, self.source, self.directory)

    def test_owner_rejects_foreign_preparation_without_mutating_originals(self):
        record = self.prepare()
        original_source = {"binaries": {"motionwright-native-provider": dict(self.binaries["native_provider"]),
                                        "motionwright-manim-runner": dict(self.binaries["manim_runner"])}}
        original_runtime = {"python": dict(self.binaries["python3"]), "ffmpeg": dict(self.binaries["ffmpeg"])}
        before = copy.deepcopy((original_source, original_runtime))
        index = {"source": self.source, "binaries": self.binaries,
                 "contract": {"native_executable_inputs": record}}
        with self.assertRaises(ValueError):
            owner.prepared_graph_manifest_inputs(original_source, original_runtime, index)
        self.assertEqual((original_source, original_runtime), before)


if __name__ == "__main__":
    unittest.main()
