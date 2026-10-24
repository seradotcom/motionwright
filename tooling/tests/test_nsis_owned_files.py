from __future__ import annotations

import codecs
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).absolute().parents[1] / "nsis_owned_files.py"
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(Path(__file__).absolute().parent))

from source_lock_admission import distribution_source_context
from test_source_lock_admission import (
    distribution_fixture_snapshot, write_distribution_fixture,
)

SPEC = importlib.util.spec_from_file_location("nsis_owned_files_tested", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
owned = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owned)


def _configured_runtime_root() -> Path:
    configured = os.environ.get("MOTIONWRIGHT_TEST_ROOT")
    return Path(configured or tempfile.gettempdir()).resolve(strict=True)


# Resolve the runtime once. Every test explicitly places its temporaries here.
RUNTIME_ROOT = _configured_runtime_root()


class OwnedFilesTests(unittest.TestCase):
    def temporary(self):
        return tempfile.TemporaryDirectory(dir=RUNTIME_ROOT, prefix="mw-nsis-owned-")

    def paths(self, root: Path):
        return root / "source.bin", root / "capture.bin", root / "capture.nsh"

    def test_exact_capture_and_include_bind_the_owned_bytes(self):
        for modified_before_capture in (False, True):
            with self.subTest(modified_before_capture=modified_before_capture), self.temporary() as directory:
                root = Path(directory)
                source = root / "source.bin"
                capture = root / "café-${UNTRUSTED}.capture"
                include = root / "capture.nsh"
                data = bytes(range(256)) * 8193 + b"\x00end"
                source.write_bytes(data)
                if modified_before_capture:
                    before_write = source.lstat()
                    with source.open("r+b") as writer:
                        writer.seek(len(data) - 1)
                        writer.write(b"Z")
                        writer.flush()
                        os.fsync(writer.fileno())
                    data = data[:-1] + b"Z"
                    os.utime(source, ns=(
                        before_write.st_atime_ns, before_write.st_mtime_ns + 2_000_000_000
                    ))
                path_before = source.lstat()
                with source.open("rb", buffering=0) as reader:
                    descriptor_before = os.fstat(reader.fileno())
                result = owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                self.assertEqual(source.read_bytes(), data)
                self.assertEqual(capture.read_bytes(), data)
                self.assertEqual(result["size"], len(data))
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
                path_after = source.lstat()
                with source.open("rb", buffering=0) as reader:
                    descriptor_after = os.fstat(reader.fileno())
                for before, after in (
                    (path_before, path_after), (descriptor_before, descriptor_after)
                ):
                    for field in (
                        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
                        "st_birthtime_ns", "st_birthtime",
                    ):
                        self.assertEqual(getattr(before, field, None),
                                         getattr(after, field, None), field)
                for field in (
                    "st_dev", "st_ino", "st_size", "st_mtime_ns",
                    "st_birthtime_ns", "st_birthtime",
                ):
                    self.assertEqual(getattr(path_before, field, None),
                                     getattr(descriptor_before, field, None), field)
                encoded = include.read_bytes()
                self.assertTrue(encoded.startswith(codecs.BOM_UTF8))
                text = encoded.decode("utf-8-sig")
                self.assertIn(
                    '!define MW_RUNTIME_SOURCE "'
                    + str(capture).replace("$", "$$") + '"\n', text
                )
                self.assertIn(f'!define MW_RUNTIME_SHA256 "{result["sha256"]}"\n', text)
                self.assertIn(f'!define MW_RUNTIME_SIZE {len(data)}\n', text)
                self.assertNotIn(str(source), text)
                self.assertFalse(list(root.glob(".mw-nsis-include-*.pending")))

    def test_real_source_changes_or_windows_rename_block_are_observed(self):
        for mode in ("same_size", "same_size_restored_mtime", "grow", "short", "replace"):
            with self.subTest(mode=mode), self.temporary() as directory:
                root = Path(directory)
                source, capture, include = self.paths(root)
                retained = root / "original-retained.bin"
                data = b"A" * (owned.COPY_CHUNK_BYTES * 2)
                source.write_bytes(data)
                initial = source.stat()
                real_read = os.read
                returned_bytes = 0
                attempted = False
                modified = False
                rename_blocked = None
                restored_observations = None

                def read_and_change(fd, size):
                    nonlocal returned_bytes, attempted, modified, rename_blocked
                    nonlocal restored_observations
                    block = real_read(fd, size)
                    returned_bytes += len(block)
                    if not block or attempted:
                        return block
                    attempted = True
                    descriptor_before_change = os.fstat(fd)
                    native_change_before = owned._source_change_time(fd)
                    if mode == "replace":
                        try:
                            source.rename(retained)
                        except OSError as error:
                            if os.name != "nt":
                                raise
                            # This is a real OS refusal, not a simulated
                            # replacement. Leave the source entirely alone.
                            rename_blocked = error
                            return block
                        source.write_bytes(data)
                        modified = True
                    else:
                        with source.open("r+b") as writer:
                            if mode in ("same_size", "same_size_restored_mtime"):
                                writer.seek(len(data) - 1)
                                writer.write(b"B")
                            elif mode == "grow":
                                writer.seek(0, os.SEEK_END)
                                writer.write(b"G" * owned.COPY_CHUNK_BYTES)
                            else:
                                writer.truncate(owned.COPY_CHUNK_BYTES // 2)
                            writer.flush()
                            os.fsync(writer.fileno())
                        modified = True
                    os.utime(source, ns=(
                        initial.st_atime_ns, initial.st_mtime_ns
                        if mode == "same_size_restored_mtime"
                        else initial.st_mtime_ns + 2_000_000_000
                    ))
                    if mode == "same_size_restored_mtime":
                        restored_observations = (
                            descriptor_before_change, os.fstat(fd), source.lstat(),
                            native_change_before, owned._source_change_time(fd),
                        )
                    return block

                result = None
                failure = None
                with mock.patch.object(owned.os, "read", side_effect=read_and_change):
                    try:
                        result = owned.capture_owned_file(
                            source, capture, include, "MW_RUNTIME"
                        )
                    except owned.CaptureError as error:
                        failure = error

                self.assertTrue(attempted)
                if mode == "same_size_restored_mtime":
                    self.assertIsNotNone(restored_observations)
                    fd_before, fd_after, path_after, change_before, change_after = restored_observations
                    for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"):
                        self.assertEqual(getattr(fd_before, field), getattr(fd_after, field), field)
                    self.assertEqual(path_after.st_mtime_ns, initial.st_mtime_ns)
                    self.assertIs(type(change_before), int)
                    self.assertIs(type(change_after), int)
                    self.assertGreater(change_before, 0)
                    self.assertGreater(change_after, 0)
                    self.assertNotEqual(
                        change_before, change_after,
                        "this regression requires an observed native metadata change",
                    )
                    if os.name != "nt":
                        self.assertNotEqual(
                            fd_before.st_ctime_ns, fd_after.st_ctime_ns,
                            "POSIX descriptor ctime must also observe the real mutation",
                        )
                self.assertLessEqual(returned_bytes, initial.st_size + 1)
                self.assertTrue(capture.is_file())
                self.assertLessEqual(capture.stat().st_size, initial.st_size)
                if rename_blocked is not None:
                    self.assertEqual(os.name, "nt")
                    self.assertEqual(mode, "replace")
                    self.assertFalse(modified)
                    self.assertFalse(retained.exists())
                    self.assertEqual(source.read_bytes(), data)
                    self.assertEqual(capture.read_bytes(), data)
                    self.assertIsNone(failure)
                    self.assertIsNotNone(result)
                    self.assertTrue(include.is_file())
                    self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
                else:
                    self.assertTrue(modified, "must exercise an actual filesystem change")
                    self.assertIsNone(result)
                    self.assertIsNotNone(failure)
                    self.assertFalse(include.exists())
                self.assertFalse(list(root.glob(".mw-nsis-include-*.pending")))

    def test_unavailable_change_time_never_publishes_an_include(self):
        # Observe a real source; inject only failure to obtain its generation.
        # Failure at either recheck must retain the already copied bytes.
        for failed_observation in (1, 2, 3):
            with self.subTest(observation=failed_observation), self.temporary() as directory:
                root = Path(directory)
                source, capture, include = self.paths(root)
                original = b"owned source whose generation must remain observable"
                source.write_bytes(original)
                real_change_time = owned._source_change_time
                observations = 0

                def observed_change_time(fd):
                    nonlocal observations
                    observations += 1
                    if observations == failed_observation:
                        raise owned.CaptureError("change-time query unavailable")
                    return real_change_time(fd)

                with mock.patch.object(
                    owned, "_source_change_time", side_effect=observed_change_time
                ):
                    with self.assertRaisesRegex(owned.CaptureError, "query unavailable"):
                        owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                self.assertEqual(observations, failed_observation)
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(include.exists())
                if failed_observation == 1:
                    self.assertFalse(capture.exists())
                else:
                    self.assertEqual(capture.read_bytes(), original)
                self.assertFalse(list(root.glob(".mw-nsis-include-*.pending")))

    def test_existing_outputs_are_never_overwritten_even_when_empty(self):
        for destination in ("capture", "include"):
            for original in (b"existing bytes", b""):
                with self.subTest(destination=destination, original=original):
                    with self.temporary() as directory:
                        root = Path(directory)
                        source, capture, include = self.paths(root)
                        source.write_bytes(b"new source")
                        existing = capture if destination == "capture" else include
                        existing.write_bytes(original)
                        with self.assertRaises(FileExistsError):
                            owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                        self.assertEqual(existing.read_bytes(), original)
                        self.assertEqual(source.read_bytes(), b"new source")
                        other = include if destination == "capture" else capture
                        self.assertFalse(other.exists())

    def test_concurrent_include_creation_is_not_replaced(self):
        with self.temporary() as directory:
            root = Path(directory)
            source, capture, include = self.paths(root)
            source.write_bytes(b"verified capture")
            real_link = os.link

            def competing_link(src, dst, **kwargs):
                with open(dst, "xb") as competing:
                    competing.write(b"other owner's include")
                return real_link(src, dst, **kwargs)

            with mock.patch.object(owned.os, "link", side_effect=competing_link):
                with self.assertRaises(FileExistsError):
                    owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
            self.assertEqual(capture.read_bytes(), b"verified capture")
            self.assertEqual(include.read_bytes(), b"other owner's include")
            pending = list(root.glob(".mw-nsis-include-*.pending"))
            self.assertEqual(len(pending), 1)
            self.assertTrue(pending[0].read_bytes().startswith(codecs.BOM_UTF8))

    def test_path_and_symbol_injection_rejected_before_outputs(self):
        with self.temporary() as directory:
            root = Path(directory)
            source, capture, include = self.paths(root)
            source.write_bytes(b"source")
            for symbol in (
                "MW_", "MW_bad", "OTHER_RUNTIME", "MW_" + "A" * 49,
                'MW_A"\n!system dangerous', "MW_A${OTHER}",
            ):
                with self.subTest(symbol=repr(symbol)):
                    with self.assertRaises(owned.CaptureError):
                        owned.capture_owned_file(source, capture, include, symbol)
                    self.assertFalse(capture.exists())
                    self.assertFalse(include.exists())
            for control in ("\x00", "\n", "\r", "\t", "\x7f", "\x85", "\u2028", "\u202e"):
                for which in ("source", "capture", "include"):
                    with self.subTest(control=repr(control), which=which):
                        values = {"source": source, "capture": capture, "include": include}
                        values[which] = str(root / ("bad" + control + "path"))
                        with self.assertRaises(owned.CaptureError):
                            owned.capture_owned_file(
                                values["source"], values["capture"], values["include"], "MW_RUNTIME"
                            )
                        self.assertFalse(capture.exists())
                        self.assertFalse(include.exists())
            self.assertEqual(owned._nsis_escape('a"$b'), 'a$\\"$$b')

    def test_devices_ads_and_ambiguous_paths_are_rejected_before_io(self):
        with self.temporary() as directory:
            root = Path(directory)
            source, capture, include = self.paths(root)
            source.write_bytes(b"source")
            bad_paths = [
                r"\\.\NUL", r"\\?\C:\files\capture.bin",
                r"\??\C:\files\capture.bin", r"\Device\HarddiskVolume1\capture.bin",
                r"\\server\share\capture.bin", r"C:relative.bin",
                str(root / "file.bin:payload"), str(root / "file.bin::$DATA"),
                str(root / "NUL"), str(root / "CON.txt"), str(root / "AUX.bin"),
                str(root / "COM1.log"), str(root / "LPT¹.bin"),
                str(root / "CONOUT$"), str(root / "trailing."),
                str(root / "trailing "), str(root / "wild*.bin"),
                str(root / "wild?.bin"), str(root / "child" / ".." / "capture.bin"),
            ]
            for bad in bad_paths:
                with self.subTest(path=repr(bad)):
                    with self.assertRaises(owned.CaptureError):
                        owned.capture_owned_file(source, bad, include, "MW_RUNTIME")
                    self.assertFalse(capture.exists())
                    self.assertFalse(include.exists())
                    self.assertEqual(source.read_bytes(), b"source")

    def test_real_source_and_parent_links_are_rejected(self):
        with self.temporary() as directory:
            root = Path(directory)
            source, capture, include = self.paths(root)
            target = root / "target.bin"
            target.write_bytes(b"target")
            target_dir = root / "target-dir"
            target_dir.mkdir()
            parent_link = root / "linked-parent"
            try:
                source.symlink_to(target)
                parent_link.symlink_to(target_dir, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("this host cannot create the real symlink/reparse fixtures")
            self.assertTrue(owned._is_link_or_reparse(source.lstat()))
            self.assertTrue(owned._is_link_or_reparse(parent_link.lstat()))
            with self.assertRaises(owned.CaptureError):
                owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
            with self.assertRaises(owned.CaptureError):
                owned.capture_owned_file(
                    target, parent_link / "capture.bin", include, "MW_RUNTIME"
                )
            self.assertFalse(capture.exists())
            self.assertFalse(include.exists())
            self.assertFalse((target_dir / "capture.bin").exists())
            self.assertEqual(target.read_bytes(), b"target")


class PredecessorProfileTests(unittest.TestCase):
    """Synthetic build inputs exercise the existing capture and include path.

    These bytes are not packages, human evidence, or native installer results.
    """

    def temporary(self):
        return tempfile.TemporaryDirectory(dir=RUNTIME_ROOT, prefix="mw-nsis-previous-")

    def fixture(self, root):
        (root / "inputs").mkdir()
        output = root / "output"
        output.mkdir()
        data = {
            "package.bin": b"synthetic previous package, never an executable",
            "receipt.json": b'{"schema":"synthetic.capture-only/1","human_acceptance":false}\n',
            "main.bin": b"synthetic previous main",
            "self.bin": b"synthetic previous self",
            "resource-a.bin": b"synthetic resource A",
            "resource-b.bin": b"synthetic resource B",
            "external-a.bin": b"synthetic external A",
            "external-b.bin": b"synthetic external B",
        }
        for name, payload in data.items():
            (root / "inputs" / name).write_bytes(payload)

        def input_record(name):
            return {
                "path": "inputs/" + name,
                "sha256": hashlib.sha256(data[name]).hexdigest(),
                "bytes": len(data[name]),
            }

        rows = [
            ("legacy-main.exe", "main.bin", "main"),
            ("uninstall.exe", "self.bin", "uninstaller"),
            ("assets/caf\u00e9-${UNTRUSTED}.dat", "resource-a.bin", "resource"),
            ("assets/second.dat", "resource-b.bin", "resource"),
            ("external-a.exe", "external-a.bin", "external_binary"),
            ("external-b.exe", "external-b.bin", "external_binary"),
        ]
        records = []
        for relative, name, kind in rows:
            item = input_record(name)
            records.append({
                "relative": relative, "source": item["path"],
                "sha256": item["sha256"], "bytes": item["bytes"], "kind": kind,
            })
        document = {
            "schema": "motionwright.nsis-predecessor/1",
            "identity": {
                "application": "FixtureMotionwright",
                "bundle_id": "test.motionwright",
                "arch": "x64",
            },
            "version": "0.1.0",
            "source_sha": "1" * 40,
            "source_tree": "2" * 40,
            "target": {
                "version": "0.2.0",
                "main_sha256": hashlib.sha256(b"synthetic new main").hexdigest(),
            },
            "package": input_record("package.bin"),
            "capture_receipt": input_record("receipt.json"),
            "files": records,
        }
        manifest = root / "predecessor.json"
        environment = self.pin(manifest, document)
        arguments = {
            **document["identity"],
            "target_version": document["target"]["version"],
            "target_main_sha256": document["target"]["main_sha256"],
        }
        return manifest, output / "predecessor.nsh", document, environment, arguments, data

    def pin(self, manifest, document):
        raw = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        manifest.write_bytes(raw)
        return {
            owned.PREDECESSOR_ENV: str(manifest),
            owned.PREDECESSOR_SHA_ENV: hashlib.sha256(raw).hexdigest(),
        }

    def assert_inputs_unchanged(self, root, data):
        for name, payload in data.items():
            self.assertEqual((root / "inputs" / name).read_bytes(), payload)

    def test_profile_binds_all_physical_inputs_and_emits_only_declared_ownership(self):
        with self.temporary() as directory:
            root = Path(directory)
            manifest, include, document, environment, arguments, data = self.fixture(root)
            raw = manifest.read_bytes()
            result = owned.emit_predecessor_include(include, environment=environment, **arguments)
            self.assertEqual(result, {
                "enabled": True, "files": 6,
                "manifest_sha256": environment[owned.PREDECESSOR_SHA_ENV],
                "package_sha256": document["package"]["sha256"],
            })
            self.assert_inputs_unchanged(root, data)
            self.assertEqual(manifest.read_bytes(), raw)
            self.assertEqual((include.parent / "mw-predecessor-manifest.capture.json").read_bytes(), raw)
            self.assertEqual((include.parent / "mw-predecessor-package.capture.bin").read_bytes(), data["package.bin"])
            self.assertEqual((include.parent / "mw-predecessor-receipt.capture.bin").read_bytes(), data["receipt.json"])
            for index, record in enumerate(document["files"]):
                self.assertEqual(
                    (include.parent / f"mw-predecessor-file-{index:04d}.capture.bin").read_bytes(),
                    (root / record["source"]).read_bytes(),
                )
            encoded = include.read_bytes()
            self.assertTrue(encoded.startswith(codecs.BOM_UTF8))
            text = encoded.decode("utf-8-sig")
            self.assertIn("!define MWPREV_ENABLED 1\n", text)
            self.assertIn("!macro MWPREV_LOOKUP Relative\n", text)
            self.assertIn('${If} "${Relative}" == "legacy-main.exe"', text)
            self.assertIn('${ElseIf} "${Relative}" == "uninstall.exe"', text)
            self.assertIn('assets\\caf\u00e9-$${UNTRUSTED}.dat', text)
            self.assertEqual(text.count('!insertmacro ${Action} '), 6)
            for record in document["files"]:
                self.assertIn(record["sha256"], text)
            self.assertNotIn(str(root), text)
            self.assertNotIn("ExecWait", text)
            self.assertNotIn("\nDelete ", text)

    def test_absent_profile_produces_an_exclusive_disabled_include(self):
        with self.temporary() as directory:
            root = Path(directory)
            _, include, _, _, arguments, _ = self.fixture(root)
            result = owned.emit_predecessor_include(include, environment={}, **arguments)
            before = include.read_bytes()
            self.assertEqual(result, {
                "enabled": False, "files": 0,
                "manifest_sha256": None, "package_sha256": None,
            })
            text = before.decode("utf-8-sig")
            self.assertIn("!define MWPREV_ENABLED 0\n", text)
            self.assertNotIn("!insertmacro ${Action}", text)
            with self.assertRaises(FileExistsError):
                owned.emit_predecessor_include(include, environment={}, **arguments)
            self.assertEqual(include.read_bytes(), before)

    def test_missing_or_changed_independent_manifest_pin_never_publishes_authority(self):
        for case in ("no_pin", "no_path", "different_pin", "changed_manifest"):
            with self.subTest(case=case), self.temporary() as directory:
                root = Path(directory)
                manifest, include, _, environment, arguments, data = self.fixture(root)
                if case == "no_pin":
                    del environment[owned.PREDECESSOR_SHA_ENV]
                elif case == "no_path":
                    del environment[owned.PREDECESSOR_ENV]
                elif case == "different_pin":
                    environment[owned.PREDECESSOR_SHA_ENV] = "0" * 64
                else:
                    manifest.write_bytes(manifest.read_bytes() + b" ")
                with self.assertRaises(owned.CaptureError):
                    owned.emit_predecessor_include(include, environment=environment, **arguments)
                self.assertFalse(include.exists())
                self.assert_inputs_unchanged(root, data)

    def test_profile_for_another_target_cannot_authorize_this_installer(self):
        for field in ("application", "bundle_id", "arch", "target_version", "target_main_sha256"):
            with self.subTest(field=field), self.temporary() as directory:
                root = Path(directory)
                _, include, _, environment, arguments, data = self.fixture(root)
                arguments[field] = "0" * 64 if field == "target_main_sha256" else "another-target"
                with self.assertRaises(owned.CaptureError):
                    owned.emit_predecessor_include(include, environment=environment, **arguments)
                self.assertFalse(include.exists())
                self.assert_inputs_unchanged(root, data)

    def test_changed_package_receipt_or_owned_bytes_are_rejected_with_captures_retained(self):
        names = (
            "package.bin", "receipt.json", "main.bin", "self.bin",
            "resource-a.bin", "resource-b.bin", "external-a.bin", "external-b.bin",
        )
        for name in names:
            with self.subTest(name=name), self.temporary() as directory:
                root = Path(directory)
                _, include, _, environment, arguments, data = self.fixture(root)
                data[name] = bytes([data[name][0] ^ 1]) + data[name][1:]
                (root / "inputs" / name).write_bytes(data[name])
                with self.assertRaises(owned.CaptureError):
                    owned.emit_predecessor_include(include, environment=environment, **arguments)
                self.assertFalse(include.exists())
                self.assertTrue((include.parent / "mw-predecessor-manifest.capture.json").is_file())
                self.assert_inputs_unchanged(root, data)

    def test_owned_aliases_role_confusion_missing_self_and_unsafe_paths_are_rejected(self):
        cases = (
            "case_alias", "self_role", "self_name", "missing_self", "unknown_kind",
            "nontext_kind", "traversal", "absolute", "ads", "dot_component", "dos_device",
            "invalid_character", "empty_executable", "boolean_size",
        )
        for case in cases:
            with self.subTest(case=case), self.temporary() as directory:
                root = Path(directory)
                manifest, include, document, _, arguments, data = self.fixture(root)
                row = document["files"][2]
                if case == "case_alias":
                    row["relative"] = "LEGACY-MAIN.EXE"
                elif case == "self_role":
                    document["files"][1]["kind"] = "resource"
                elif case == "self_name":
                    document["files"][1]["relative"] = "other-self.exe"
                elif case == "missing_self":
                    del document["files"][1]
                elif case == "unknown_kind":
                    row["kind"] = "project"
                elif case == "nontext_kind":
                    row["kind"] = []
                elif case == "empty_executable":
                    document["files"][0]["bytes"] = 0
                elif case == "boolean_size":
                    row["bytes"] = True
                else:
                    row["relative"] = {
                        "traversal": "../escape.bin",
                        "absolute": "C:/escape.bin",
                        "ads": "asset.bin:stream",
                        "dot_component": "assets/./item.bin",
                        "dos_device": "assets/NUL.txt",
                        "invalid_character": 'assets/"item.bin',
                    }[case]
                environment = self.pin(manifest, document)
                with self.assertRaises(owned.CaptureError):
                    owned.emit_predecessor_include(include, environment=environment, **arguments)
                self.assertFalse(include.exists())
                self.assert_inputs_unchanged(root, data)

    def test_duplicate_json_keys_and_non_file_inputs_do_not_produce_an_include(self):
        for case in ("duplicate_key", "source_directory", "source_traversal"):
            with self.subTest(case=case), self.temporary() as directory:
                root = Path(directory)
                manifest, include, document, environment, arguments, data = self.fixture(root)
                if case == "duplicate_key":
                    raw = manifest.read_bytes().replace(
                        b'"schema": "motionwright.nsis-predecessor/1"',
                        b'"schema": "motionwright.nsis-predecessor/1", "schema": "motionwright.nsis-predecessor/1"',
                        1,
                    )
                    manifest.write_bytes(raw)
                    environment[owned.PREDECESSOR_SHA_ENV] = hashlib.sha256(raw).hexdigest()
                else:
                    document["files"][2]["source"] = "inputs" if case == "source_directory" else "../outside.bin"
                    environment = self.pin(manifest, document)
                with self.assertRaises((owned.CaptureError, OSError)):
                    owned.emit_predecessor_include(include, environment=environment, **arguments)
                self.assertFalse(include.exists())
                self.assert_inputs_unchanged(root, data)

    def test_preexisting_capture_is_not_replaced_or_reused(self):
        with self.temporary() as directory:
            root = Path(directory)
            _, include, _, environment, arguments, data = self.fixture(root)
            existing = include.parent / "mw-predecessor-package.capture.bin"
            existing.write_bytes(b"unowned existing capture")
            with self.assertRaises(FileExistsError):
                owned.emit_predecessor_include(include, environment=environment, **arguments)
            self.assertEqual(existing.read_bytes(), b"unowned existing capture")
            self.assertFalse(include.exists())
            self.assert_inputs_unchanged(root, data)

    def test_real_cli_uses_the_pinned_profile_and_rejects_argument_mode_confusion(self):
        with self.temporary() as directory:
            root = Path(directory)
            _, include, _, environment, arguments, data = self.fixture(root)
            command = [
                sys.executable, str(SCRIPT), "--emit-predecessor", "--include", str(include),
                "--application", arguments["application"],
                "--bundle-id", arguments["bundle_id"], "--arch", arguments["arch"],
                "--target-version", arguments["target_version"],
                "--target-main-sha256", arguments["target_main_sha256"],
            ]
            result = subprocess.run(
                command, cwd=root, env={**os.environ, **environment},
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["files"], 6)
            before = include.read_bytes()
            refused = subprocess.run(
                [*command, "--source", "untrusted"], cwd=root,
                env={**os.environ, **environment},
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("cannot be mixed", refused.stderr)
            self.assertEqual(include.read_bytes(), before)
            self.assert_inputs_unchanged(root, data)




class PublicationIntegrityTests(unittest.TestCase):
    """Exercise the real owned files at the capture/publication boundary."""

    def temporary(self):
        return tempfile.TemporaryDirectory(dir=RUNTIME_ROOT, prefix="mw-nsis-publication-")

    def fixture(self, directory):
        root = Path(directory)
        source, capture, include = (root / name for name in (
            "source.bin", "capture.bin", "capture.nsh"))
        data = b"Isolated non-executable NSIS integrity fixture.\n" + bytes(range(256)) * 16
        source.write_bytes(data)
        return root, source, capture, include, data, source.lstat()

    def assert_source_unchanged(self, source, data, initial):
        self.assertEqual(source.read_bytes(), data)
        final = source.lstat()
        # Reads may update access time. All stable identity/content fields stay bound.
        for field in ("st_dev", "st_ino", "st_size", "st_mode", "st_nlink",
                      "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns", "st_birthtime"):
            self.assertEqual(getattr(final, field, None), getattr(initial, field, None), field)

    def mutate_retaining_size_and_mtime(self, path):
        before = path.lstat()
        original = path.read_bytes()
        self.assertTrue(original)
        with path.open("r+b") as writer:
            writer.seek(len(original) - 1)
            self.assertEqual(writer.write(bytes([original[-1] ^ 0x80])), 1)
            writer.flush()
            os.fsync(writer.fileno())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = path.lstat()
        changed = path.read_bytes()
        self.assertEqual((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                         (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns))
        self.assertEqual(changed, original[:-1] + bytes([original[-1] ^ 0x80]))
        if os.name == "nt":
            self.assertEqual(owned._signature(before), owned._signature(after))
        return {"before": hashlib.sha256(original).hexdigest(),
                "after": hashlib.sha256(changed).hexdigest(),
                "same_identity_size_mtime": True,
                "stat_signature_equal": owned._signature(before) == owned._signature(after)}

    def test_changed_capture_with_restored_mtime_is_rejected_before_publication(self):
        with self.temporary() as directory:
            root, source, capture, include, data, initial = self.fixture(directory)
            real_capture, real_link = owned._capture, os.link
            witness = {}
            publication_calls = []

            def capture_then_change(*args, **kwargs):
                result = real_capture(*args, **kwargs)
                witness.update(self.mutate_retaining_size_and_mtime(capture))
                self.assertEqual(result[0], witness["before"])
                return result

            def observed_link(*args, **kwargs):
                publication_calls.append(str(args[1]))
                return real_link(*args, **kwargs)

            rejected = None
            with mock.patch.object(owned, "_capture", side_effect=capture_then_change), \
                    mock.patch.object(owned.os, "link", side_effect=observed_link):
                try:
                    owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                except owned.CaptureError as error:
                    rejected = error
            self.assertTrue(witness)
            self.assert_source_unchanged(source, data, initial)
            self.assertEqual(hashlib.sha256(capture.read_bytes()).hexdigest(), witness["after"])
            print("NSIS_CAPTURE_INTEGRITY_WITNESS " + json.dumps({
                **witness, "include_exists": include.exists(),
                "publication_calls": publication_calls,
                "rejection": str(rejected) if rejected is not None else None}, sort_keys=True))
            if rejected is None:
                self.assertTrue(include.exists())
                self.assertIn(witness["before"], include.read_text(encoding="utf-8-sig"))
                self.fail("NSIS_CAPTURE_MUTATION_PUBLISHED: include binds bytes preceding real mutation")
            self.assertRegex(str(rejected), r"capture.*(changed|differs|integrity|bytes)")
            self.assertFalse(include.exists())
            self.assertEqual(publication_calls, [])

    def test_changed_pending_with_restored_mtime_is_rejected_before_publication(self):
        with self.temporary() as directory:
            root, source, capture, include, data, initial = self.fixture(directory)
            real_same, real_link = owned._require_same_object, os.link
            witness, publication_calls = {}, []

            def inspect_then_change(observed, original, label):
                real_same(observed, original, label)
                if label == "pending include" and not witness:
                    pending, = root.glob(".mw-nsis-include-*.pending")
                    witness.update(self.mutate_retaining_size_and_mtime(pending))

            def observed_link(*args, **kwargs):
                publication_calls.append(str(args[1]))
                return real_link(*args, **kwargs)

            rejected = None
            with mock.patch.object(owned, "_require_same_object", side_effect=inspect_then_change), \
                    mock.patch.object(owned.os, "link", side_effect=observed_link):
                try:
                    owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                except owned.CaptureError as error:
                    rejected = error
            self.assertTrue(witness)
            self.assert_source_unchanged(source, data, initial)
            self.assertEqual(capture.read_bytes(), data)
            print("NSIS_PENDING_INTEGRITY_WITNESS " + json.dumps({
                **witness, "include_exists": include.exists(),
                "publication_calls": publication_calls,
                "rejection": str(rejected) if rejected is not None else None}, sort_keys=True))
            if rejected is None:
                self.assertTrue(include.exists())
                self.assertEqual(hashlib.sha256(include.read_bytes()).hexdigest(), witness["after"])
                self.fail("NSIS_PENDING_MUTATION_PUBLISHED: changed include bytes became authoritative")
            self.assertRegex(str(rejected), r"pending include.*(changed|differs|integrity|bytes)")
            self.assertFalse(include.exists())
            self.assertEqual(publication_calls, [])

    @unittest.skipUnless(os.name == "nt", "requires real Windows sharing semantics")
    def test_windows_guards_block_writes_and_capture_deletion_through_publication(self):
        with self.temporary() as directory:
            root, source, capture, include, data, initial = self.fixture(directory)
            alias = root / "capture-alias.bin"
            real_capture, real_link = owned._capture, os.link
            attempts = {}

            def capture_with_alias(*args, **kwargs):
                result = real_capture(*args, **kwargs)
                real_link(capture, alias, follow_symlinks=False)
                return result

            def probe_then_link(src, dst, **kwargs):
                for label, path in (("capture", capture), ("alias", alias), ("pending", Path(src))):
                    try:
                        with path.open("r+b") as writer:
                            first = writer.read(1)
                            writer.seek(0)
                            writer.write(bytes([first[0] ^ 0x80]))
                            writer.flush()
                            os.fsync(writer.fileno())
                    except PermissionError:
                        attempts[label] = "WRITE_DENIED"
                    else:
                        attempts[label] = "WRITE_ALLOWED"
                try:
                    capture.unlink()
                except PermissionError:
                    attempts["capture_delete"] = "DELETE_DENIED"
                else:
                    attempts["capture_delete"] = "DELETE_ALLOWED"
                return real_link(src, dst, **kwargs)

            with mock.patch.object(owned, "_capture", side_effect=capture_with_alias), \
                    mock.patch.object(owned.os, "link", side_effect=probe_then_link):
                owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
            self.assert_source_unchanged(source, data, initial)
            print("NSIS_PUBLICATION_GUARD_WITNESS " + json.dumps(attempts, sort_keys=True))
            self.assertEqual(attempts, {"capture": "WRITE_DENIED", "alias": "WRITE_DENIED",
                                       "pending": "WRITE_DENIED", "capture_delete": "DELETE_DENIED"},
                             "NSIS_PUBLICATION_WRITERS_ALLOWED")
            self.assertEqual(capture.read_bytes(), data)
            self.assertIn(hashlib.sha256(data).hexdigest(), include.read_text(encoding="utf-8-sig"))
            # Both guards must have closed before control returns.
            with capture.open("r+b"), include.open("r+b"):
                pass
            alias.unlink()

    @unittest.skipUnless(os.name == "nt", "requires real Windows sharing semantics")
    def test_windows_preexisting_capture_writer_is_rejected_before_publication(self):
        with self.temporary() as directory:
            root, source, capture, include, data, initial = self.fixture(directory)
            real_capture = owned._capture
            writers = []

            def capture_with_writer(*args, **kwargs):
                result = real_capture(*args, **kwargs)
                writers.append(capture.open("r+b"))
                return result

            rejected = None
            try:
                with mock.patch.object(owned, "_capture", side_effect=capture_with_writer):
                    try:
                        owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                    except owned.CaptureError as error:
                        rejected = error
            finally:
                for writer in writers:
                    writer.close()
            self.assertEqual(len(writers), 1)
            self.assert_source_unchanged(source, data, initial)
            if rejected is None:
                self.assertTrue(include.exists())
                self.fail("NSIS_CAPTURE_WITH_WRITER_PUBLISHED: writer coexisted with publication")
            self.assertRegex(str(rejected), r"capture.*publication")
            self.assertFalse(include.exists())
            with capture.open("r+b"):
                pass

    def test_publication_failure_releases_handles_and_retains_owned_files(self):
        with self.temporary() as directory:
            root, source, capture, include, data, initial = self.fixture(directory)
            with mock.patch.object(owned.os, "link", side_effect=OSError("forced publication failure")):
                with self.assertRaisesRegex(OSError, "forced publication failure"):
                    owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
            self.assertFalse(include.exists())
            self.assertEqual(capture.read_bytes(), data)
            self.assert_source_unchanged(source, data, initial)
            pending, = root.glob(".mw-nsis-include-*.pending")
            for path in (capture, pending):
                with path.open("r+b"):
                    pass
                moved = path.with_name(path.name + ".retained")
                path.rename(moved)
                self.assertTrue(moved.exists())

    def test_empty_capture_publishes_the_exact_empty_digest(self):
        with self.temporary() as directory:
            root = Path(directory)
            source, capture, include = (root / name for name in ("source", "capture", "include"))
            source.write_bytes(b"")
            result = owned.capture_owned_file(source, capture, include, "MW_EMPTY")
            self.assertEqual(capture.read_bytes(), b"")
            self.assertEqual(result["size"], 0)
            self.assertEqual(result["sha256"], hashlib.sha256(b"").hexdigest())
            self.assertIn(result["sha256"], include.read_text(encoding="utf-8-sig"))


    def test_changed_predecessor_capture_is_rejected_before_publication(self):
        with self.temporary() as directory:
            root = Path(directory)
            fixtures = PredecessorProfileTests(
                "test_profile_binds_all_physical_inputs_and_emits_only_declared_ownership")
            manifest, include, document, environment, arguments, data = fixtures.fixture(root)
            original_manifest = manifest.read_bytes()
            target = include.parent / "mw-predecessor-file-0000.capture.bin"
            real_capture, real_link = owned._capture, os.link
            witness, publications = {}, []

            def capture_then_change(source, capture):
                result = real_capture(source, capture)
                if capture == target:
                    witness.update(self.mutate_retaining_size_and_mtime(capture))
                    self.assertEqual(result[0], witness["before"])
                return result

            def observed_link(*args, **kwargs):
                publications.append(str(args[1]))
                return real_link(*args, **kwargs)

            rejected = None
            with mock.patch.object(owned, "_capture", side_effect=capture_then_change), \
                    mock.patch.object(owned.os, "link", side_effect=observed_link):
                try:
                    owned.emit_predecessor_include(include, environment=environment, **arguments)
                except owned.CaptureError as error:
                    rejected = error
            self.assertTrue(witness)
            fixtures.assert_inputs_unchanged(root, data)
            self.assertEqual(manifest.read_bytes(), original_manifest)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), witness["after"])
            print("NSIS_PREDECESSOR_INTEGRITY_WITNESS " + json.dumps({
                **witness, "include_exists": include.exists(), "publication_calls": publications,
                "rejection": str(rejected) if rejected is not None else None}, sort_keys=True))
            if rejected is None:
                self.assertTrue(include.exists())
                self.assertIn(document["files"][0]["sha256"], include.read_text(encoding="utf-8-sig"))
                self.fail("NSIS_PREDECESSOR_MUTATION_PUBLISHED: owned capture no longer matches its pin")
            self.assertRegex(str(rejected), r"predecessor capture.*(changed|differs|bytes)")
            self.assertFalse(include.exists())
            self.assertEqual(publications, [])

    def test_capture_replacement_or_size_change_never_publishes(self):
        for mode in ("replace", "grow", "truncate"):
            with self.subTest(mode=mode), self.temporary() as directory:
                root, source, capture, include, data, initial = self.fixture(directory)
                real_capture = owned._capture
                injected = []

                def capture_then_change(*args, **kwargs):
                    result = real_capture(*args, **kwargs)
                    if mode == "replace":
                        capture.rename(root / "captured-original.bin")
                        capture.write_bytes(data)
                    elif mode == "grow":
                        with capture.open("ab") as writer:
                            writer.write(b"X")
                    else:
                        with capture.open("r+b") as writer:
                            writer.truncate(len(data) - 1)
                    injected.append(mode)
                    return result

                with mock.patch.object(owned, "_capture", side_effect=capture_then_change):
                    with self.assertRaisesRegex(owned.CaptureError, "capture"):
                        owned.capture_owned_file(source, capture, include, "MW_RUNTIME")
                self.assertEqual(injected, [mode])
                self.assertFalse(include.exists())
                self.assert_source_unchanged(source, data, initial)



class DistributionOwnedFilesTests(unittest.TestCase):
    """Synthetic contract fixtures; no actual distribution permission is issued."""

    def temporary(self):
        return tempfile.TemporaryDirectory(dir=RUNTIME_ROOT, prefix="mw-nsis-distribution-")

    def fixture(self, directory, *, status="unresolved"):
        root = Path(directory).resolve(strict=True) / "consumer"
        fixture = write_distribution_fixture(root, status=status)
        tooling = root / "tooling"
        tooling.mkdir()
        # The real CLI derives its source root from its own script, never cwd.
        for source in (SCRIPT, SCRIPT.with_name("source_lock_admission.py")):
            (tooling / source.name).write_bytes(source.read_bytes())
        working = root / "unrelated-cwd"
        working.mkdir()
        (working / "SOURCE_LOCK.json").write_bytes(b"not the authoritative source root\n")
        temporary = root / "tmp"
        temporary.mkdir()
        source = root / "main.bin"
        source.write_bytes(fixture["payload_bytes"])
        environment = dict(os.environ)
        for name in (
            "MW_NSIS_DISTRIBUTION_ADMISSION", "MW_NSIS_DISTRIBUTION_ADMISSION_SHA256",
            "MW_NSIS_PREDECESSOR_MANIFEST", "MW_NSIS_PREDECESSOR_MANIFEST_SHA256",
        ):
            environment.pop(name, None)
        environment.update({
            "MW_NSIS_DISTRIBUTION_ADMISSION": fixture["record_path"],
            "MW_NSIS_DISTRIBUTION_ADMISSION_SHA256": fixture["record_sha256"],
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
        })
        return {
            **fixture, "root": root, "source": source, "cwd": working,
            "script": tooling / SCRIPT.name, "environment": environment,
        }

    def cli(self, fixture, *, mode=True, arch="x64", symbol="MW_MAIN",
            environment=None, stem="selected", extra=()):
        root = fixture["root"]
        capture = root / (stem + ".capture.bin")
        include = root / (stem + ".nsh")
        command = [
            sys.executable, "-B", str(fixture["script"]),
            "--source", str(fixture["source"]), "--capture", str(capture),
            "--include", str(include), "--symbol", symbol,
        ]
        if mode:
            command.append("--prepare-distribution")
        if arch is not None:
            command.extend(["--arch", arch])
        command.extend(extra)
        result = subprocess.run(
            command, cwd=fixture["cwd"],
            env=fixture["environment"] if environment is None else environment,
            capture_output=True, text=True, timeout=30, check=False,
        )
        return result, capture, include

    def test_real_cli_negative_refuses_before_outputs_and_independent_custody_continues(self):
        for status in ("unresolved", "prohibited"):
            with self.subTest(status=status), self.temporary() as directory:
                fixture = self.fixture(directory, status=status)
                root = fixture["root"]
                context = distribution_source_context(root)
                self.assertEqual(context, fixture["document"]["source"])
                before = distribution_fixture_snapshot(root)
                refused, capture, include = self.cli(fixture)
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertIn("distribution_admission_" + status, refused.stderr)
                self.assertFalse(capture.exists())
                self.assertFalse(include.exists())
                self.assertEqual(distribution_fixture_snapshot(root), before)
                self.assertEqual(distribution_source_context(root), context)

                # This is unrelated custody of fixture bytes, not a disposition.
                continued, capture, include = self.cli(
                    fixture, mode=False, arch=None, symbol="MW_RUNTIME", stem="custody",
                )
                self.assertEqual(continued.returncode, 0, continued.stderr)
                self.assertEqual(capture.read_bytes(), fixture["payload_bytes"])
                self.assertIn("!define MW_RUNTIME_SHA256", include.read_text(encoding="utf-8-sig"))
                self.assertEqual(distribution_source_context(root), context)
                self.assertEqual(fixture["source"].read_bytes(), fixture["payload_bytes"])

    def test_real_cli_synthetic_permitted_control_binds_bytes_not_actual_rights(self):
        with self.temporary() as directory:
            fixture = self.fixture(directory, status="permitted")
            result, capture, include = self.cli(fixture)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(capture.read_bytes(), fixture["payload_bytes"])
            digest = hashlib.sha256(fixture["payload_bytes"]).hexdigest()
            text = include.read_text(encoding="utf-8-sig")
            self.assertIn(f'!define MW_MAIN_SHA256 "{digest}"', text)
            self.assertIn(f'!define MW_MAIN_SIZE {len(fixture["payload_bytes"])}', text)
            self.assertEqual(
                distribution_source_context(fixture["root"]),
                fixture["document"]["source"],
            )

    def test_real_cli_main_mode_and_pinned_context_are_required_without_fallback(self):
        for case in (
            "no_mode", "no_arch", "other_symbol", "mixed_modes",
            "no_record", "no_pin", "wrong_pin", "other_arch",
        ):
            with self.subTest(case=case), self.temporary() as directory:
                fixture = self.fixture(directory, status="permitted")
                arguments = {}
                environment = dict(fixture["environment"])
                code = None
                if case == "no_mode":
                    arguments.update(mode=False, arch=None)
                    code = "distribution_admission_mode_required"
                elif case == "no_arch":
                    arguments["arch"] = None
                    code = "distribution_admission_main_and_arch_required"
                elif case == "other_symbol":
                    arguments["symbol"] = "MW_RUNTIME"
                    code = "distribution_admission_main_and_arch_required"
                elif case == "mixed_modes":
                    arguments["extra"] = ("--emit-predecessor",)
                elif case == "no_record":
                    environment.pop("MW_NSIS_DISTRIBUTION_ADMISSION")
                    code = "distribution_admission_missing"
                elif case == "no_pin":
                    environment.pop("MW_NSIS_DISTRIBUTION_ADMISSION_SHA256")
                    code = "distribution_admission_missing"
                elif case == "wrong_pin":
                    environment["MW_NSIS_DISTRIBUTION_ADMISSION_SHA256"] = "0" * 64
                    code = "distribution_admission_record_hash_mismatch"
                else:
                    arguments["arch"] = "arm64"
                    code = "distribution_admission_scope_mismatch"
                before = distribution_fixture_snapshot(fixture["root"])
                result, capture, include = self.cli(
                    fixture, environment=environment, **arguments,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                if code is not None:
                    self.assertIn(code, result.stderr)
                self.assertFalse(capture.exists())
                self.assertFalse(include.exists())
                self.assertEqual(distribution_fixture_snapshot(fixture["root"]), before)

    def test_expected_payload_mismatch_refuses_before_any_capture_or_include(self):
        for mismatch in ("size", "digest", "boolean_size"):
            with self.subTest(mismatch=mismatch), self.temporary() as directory:
                root = Path(directory).resolve(strict=True)
                source, capture, include = (
                    root / "source.bin", root / "capture.bin", root / "capture.nsh",
                )
                data = b"exact synthetic payload"
                source.write_bytes(data)
                expected = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                if mismatch == "size":
                    expected["bytes"] += 1
                elif mismatch == "digest":
                    expected["sha256"] = "0" * 64
                else:
                    expected["bytes"] = True
                before = distribution_fixture_snapshot(root)
                with self.assertRaises(owned.CaptureError) as failure:
                    owned.capture_owned_file(
                        source, capture, include, "MW_MAIN", expected_payload=expected,
                    )
                self.assertIn("distribution_admission_payload_", str(failure.exception))
                self.assertFalse(capture.exists())
                self.assertFalse(include.exists())
                self.assertEqual(distribution_fixture_snapshot(root), before)

    def test_expected_payload_is_read_on_one_retained_descriptor_before_output_creation(self):
        with self.temporary() as directory:
            root = Path(directory).resolve(strict=True)
            source, capture, include = (
                root / "source.bin", root / "capture.bin", root / "capture.nsh",
            )
            data = bytes(range(256)) * 19
            source.write_bytes(data)
            expected = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            real_open, real_read = os.open, os.read
            source_descriptors = []
            pre_capture_bytes = bytearray()
            capture_observed = False

            def observe_open(path, flags, *args, **kwargs):
                nonlocal capture_observed
                observed = Path(path)
                # The later POSIX publication guard reopens capture for reading.
                # Only output creation must observe the retained, rewound source.
                if observed == capture and flags & os.O_CREAT:
                    self.assertTrue(flags & os.O_EXCL)
                    self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR), os.O_WRONLY)
                    self.assertEqual(len(source_descriptors), 1)
                    self.assertEqual(bytes(pre_capture_bytes), data)
                    self.assertEqual(
                        os.lseek(source_descriptors[0], 0, os.SEEK_CUR), 0,
                        "the retained input descriptor must be rewound before capture",
                    )
                    self.assertFalse(capture_observed)
                    capture_observed = True
                fd = real_open(path, flags, *args, **kwargs)
                if observed == source:
                    source_descriptors.append(fd)
                return fd

            def observe_read(fd, size):
                block = real_read(fd, size)
                if source_descriptors and fd == source_descriptors[0] and not capture_observed:
                    pre_capture_bytes.extend(block)
                return block

            with (
                mock.patch.object(owned.os, "open", side_effect=observe_open),
                mock.patch.object(owned.os, "read", side_effect=observe_read),
            ):
                result = owned.capture_owned_file(
                    source, capture, include, "MW_MAIN", expected_payload=expected,
                )
            self.assertTrue(capture_observed)
            self.assertEqual(len(source_descriptors), 1)
            self.assertEqual(capture.read_bytes(), data)
            self.assertEqual(result["sha256"], expected["sha256"])
            self.assertTrue(include.is_file())

    def test_actual_source_mutation_after_prehash_cannot_publish_an_include(self):
        with self.temporary() as directory:
            root = Path(directory).resolve(strict=True)
            source, capture, include = (
                root / "source.bin", root / "capture.bin", root / "capture.nsh",
            )
            data = b"A" * 4096
            substituted = b"B" * len(data)
            source.write_bytes(data)
            expected = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            real_open = os.open
            changed = False

            def change_before_capture_open(path, flags, *args, **kwargs):
                nonlocal changed
                if Path(path) == capture:
                    self.assertFalse(changed)
                    # The real prehash has completed. Modify the same source file.
                    with source.open("r+b") as writer:
                        writer.write(substituted)
                        writer.flush()
                        os.fsync(writer.fileno())
                    changed = True
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(owned.os, "open", side_effect=change_before_capture_open):
                with self.assertRaises(owned.CaptureError) as failure:
                    owned.capture_owned_file(
                        source, capture, include, "MW_MAIN", expected_payload=expected,
                    )
            self.assertTrue(changed)
            self.assertEqual(str(failure.exception), "distribution_admission_payload_changed")
            self.assertEqual(source.read_bytes(), substituted)
            self.assertEqual(capture.read_bytes(), substituted)
            self.assertFalse(include.exists())
            self.assertFalse(list(root.glob(".mw-nsis-include-*.pending")))

    def test_actual_template_selects_required_main_mode_without_environment_interpolation(self):
        template = (
            SCRIPT.parent.parent / "apps/desktop/src-tauri/windows/installer.nsi"
        ).read_text(encoding="utf-8")
        marker = "!macro MWINST_CAPTURE Symbol Source"
        self.assertEqual(template.count(marker), 1)
        macro = template.split(marker, 1)[1].split("!macroend", 1)[0]
        main, ordinary = macro.split("!else", 1)
        self.assertIn('!if "${Symbol}" == "MW_MAIN"', main)
        main_commands = [
            line.strip() for line in main.splitlines() if line.strip().startswith("!execute ")
        ]
        ordinary_commands = [
            line.strip() for line in ordinary.splitlines() if line.strip().startswith("!execute ")
        ]
        self.assertEqual(len(main_commands), 1)
        self.assertEqual(len(ordinary_commands), 1)
        self.assertIn('--prepare-distribution --arch "${ARCH}"', main_commands[0])
        self.assertIn('--source "${Source}"', main_commands[0])
        self.assertTrue(main_commands[0].endswith("= 0"))
        self.assertNotIn("--prepare-distribution", ordinary_commands[0])
        self.assertIn('!insertmacro MWINST_CAPTURE MW_MAIN "${MAINBINARYSRCPATH}"', template)
        for command in main_commands + ordinary_commands:
            self.assertNotIn("MW_NSIS_DISTRIBUTION_ADMISSION", command)
        self.assertLess(macro.index("!execute "), macro.index("!include "))



    def test_admitted_main_capture_mutation_cannot_publish_an_include(self):
        with self.temporary() as directory:
            fixture = self.fixture(directory, status="permitted")
            root, source = fixture["root"], fixture["source"]
            capture, include = root / "admitted.capture.bin", root / "admitted.nsh"
            context = distribution_source_context(root)
            source_bytes = source.read_bytes()
            spec = importlib.util.spec_from_file_location(
                "nsis_admitted_capture_boundary_test", fixture["script"],
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            isolated = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(isolated)
            real_capture, real_link = isolated._capture, isolated.os.link
            witness, publications = {}, []
            probe = PublicationIntegrityTests(
                "test_empty_capture_publishes_the_exact_empty_digest",
            )

            def capture_admitted_then_change(source_path, capture_path, expected_payload=None):
                self.assertEqual(expected_payload, fixture["document"]["payload"])
                result = real_capture(source_path, capture_path, expected_payload)
                self.assertEqual(result[:2], (
                    hashlib.sha256(source_bytes).hexdigest(), len(source_bytes),
                ))
                witness.update(probe.mutate_retaining_size_and_mtime(capture_path))
                return result

            def observed_link(*args, **kwargs):
                publications.append(str(args[1]))
                return real_link(*args, **kwargs)

            with (
                mock.patch.dict(os.environ, fixture["environment"], clear=True),
                mock.patch.object(isolated, "_capture", side_effect=capture_admitted_then_change),
                mock.patch.object(isolated.os, "link", side_effect=observed_link),
            ):
                result = isolated.main([
                    "--prepare-distribution", "--arch", "x64",
                    "--source", str(source), "--capture", str(capture),
                    "--include", str(include), "--symbol", "MW_MAIN",
                ])
            self.assertTrue(witness, "admitted bytes must reach the real capture first")
            self.assertEqual(result, 2, "NSIS_ADMITTED_CAPTURE_MUTATION_PUBLISHED")
            self.assertEqual(publications, [])
            self.assertFalse(include.exists())
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(hashlib.sha256(capture.read_bytes()).hexdigest(), witness["after"])
            self.assertEqual(distribution_source_context(root), context)
            self.assertFalse(list(root.glob(".mw-nsis-include-*.pending")))
            print("NSIS_ADMITTED_CAPTURE_INTEGRITY_WITNESS " + json.dumps(witness, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
