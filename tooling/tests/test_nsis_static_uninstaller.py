"""Bounded parser, custody and real-file comparison controls.

Fixtures are inert unit inputs. No downloader, compiler, installer, 7-Zip,
or installed application executes here; these tests grant no acceptance credit.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tooling"))
import ci_windows_dependency_capture as gate
import nsis_static_uninstaller as static
from candidate_smoke import verify_windows_application_files


def listing(*members, subtype="NSIS-3 Unicode", physical=1234):
    header = ("7-Zip unit listing fixture; not an original observation\n\n"
              "Path = fixture-setup.exe\nType = Nsis\n"
              f"Physical Size = {physical}\nSubType = {subtype}\n\n----------\n")
    blocks = []
    for name, size in members:
        blocks.append(f"Path = {name}\nSize = {size}\nFolder = -\n")
    return (header + "\n".join(blocks)).encode("utf-8")


def context():
    return {
        "source": "1" * 40, "source_tree": "2" * 40, "run_id": "123",
        "run_attempt": 1, "base_inventory_sha256": "3" * 64,
        "setup": {"filename": "fixture-setup.exe", "bytes": 1234, "sha256": "4" * 64},
    }


class StaticUninstallerParserTests(unittest.TestCase):
    def test_solid_unknown_size_is_preserved_and_literal_path_selected(self):
        name = "$PLUGINSDIR\\" + static.MEMBER
        selected = static.select_member(listing(("main.exe", "700"), (name, "")), 1234)
        self.assertEqual(selected, {"path": name, "listed_bytes": None})
        self.assertEqual(static.select_member(listing((name, "79338")), 1234),
                         {"path": name, "listed_bytes": 79338})
        command = static.commands(Path("7z.exe"), Path("fixture-setup.exe"), name)["extract"]
        self.assertEqual(command, ["7z.exe", "x", "-so", "-spd", "-sccUTF-8",
                                   "--", "fixture-setup.exe", name])

    def test_exit_zero_diagnostics_and_incomplete_listing_are_rejected(self):
        for marker in ("NSIS-3 Unicode BadCmd=13", "Unsupported Method", "WARNING",
                       "ERRORS: 1"):
            with self.subTest(marker=marker), self.assertRaisesRegex(
                    ValueError, "nsis_static_listing_diagnostic"):
                static.select_member(listing((static.MEMBER, ""), subtype=marker), 1234)
        for raw in (b"\xff", b"", listing((static.MEMBER, "")).replace(b"----------", b""),
                    listing((static.MEMBER, "")) + b"\n----------\n"):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                static.select_member(raw, 1234)

    def test_duplicate_case_alias_traversal_and_missing_member_fail_closed(self):
        sets = [
            [("uninstall.exe", "79338")],
            [(static.MEMBER, ""), (static.MEMBER, "")],
            [(static.MEMBER, ""), (static.MEMBER.upper(), "")],
            [("$PLUGINSDIR/" + static.MEMBER, ""), ("other/" + static.MEMBER, "")],
            [("../" + static.MEMBER, "")],
            [("$PLUGINSDIR//" + static.MEMBER, "")],
            [("$PLUGINSDIR/./" + static.MEMBER, "")],
            [("/" + static.MEMBER, "")],
            [(static.MEMBER.upper(), "")],
        ]
        for members in sets:
            with self.subTest(members=members), self.assertRaises(ValueError):
                static.select_member(listing(*members), 1234)

    def test_member_size_and_archive_identity_do_not_trust_listing(self):
        for size in ("0", "-1", "63", str(static.MAX_SELF + 1), "1e6", " 79338"):
            with self.subTest(size=size), self.assertRaises(ValueError):
                static.select_member(listing((static.MEMBER, size)), 1234)
        raw = listing((static.MEMBER, "79338"))
        for changed in (
            raw.replace(b"Physical Size = 1234", b"Physical Size = 1235"),
            raw.replace(b"Type = Nsis", b"Type = 7z"),
            raw.replace(b"Folder = -", b"Folder = +"),
            raw.replace(b"Size = 79338", b"Size = 79338\nSize = 79338"),
        ):
            with self.subTest(changed=changed[-100:]), self.assertRaises(ValueError):
                static.select_member(changed, 1234)

    def test_pe_profile_rejects_dll_wrong_machine_truncation_and_oversize(self):
        # Header-only unit fixture. It is not runnable or an installer output.
        raw = bytearray(256)
        raw[:2] = b"MZ"
        struct.pack_into("<I", raw, 60, 64)
        raw[64:68] = b"PE\0\0"
        struct.pack_into("<HH", raw, 68, 0x14C, 1)
        struct.pack_into("<HH", raw, 84, 96, 2)
        struct.pack_into("<H", raw, 88, 0x10B)
        self.assertEqual(static.inspect_pe(bytes(raw)),
                         {"format": "PE", "machine": "0x14c", "sections": 1})
        for offset, value in ((68, 0xAA64), (86, 0x2002), (88, 0x20B), (70, 0)):
            changed = bytearray(raw)
            struct.pack_into("<H", changed, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                static.inspect_pe(bytes(changed))
        for changed in (b"", bytes(raw[:87]), bytes(raw) + b"\0" * static.MAX_SELF):
            with self.subTest(length=len(changed)), self.assertRaises(ValueError):
                static.inspect_pe(changed)

    def test_context_is_exact_first_attempt_and_setup_identity(self):
        self.assertEqual(static.validate_context(context()), context())
        for key, value in (("run_attempt", 2), ("run_attempt", True), ("run_id", "0"),
                           ("source", "a" * 39), ("base_inventory_sha256", "not-a-seal"),
                           ("extra", "unadmitted")):
            changed = {**context(), key: value}
            with self.subTest(field=key, value=value), self.assertRaises(ValueError):
                static.validate_context(changed)
        for key, value in (("filename", "../fixture-setup.exe"), ("bytes", True),
                           ("bytes", 0), ("sha256", "A" * 64)):
            changed = context()
            changed["setup"][key] = value
            with self.subTest(setup=key), self.assertRaises(ValueError):
                static.validate_context(changed)


class StaticUninstallerCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-static-nsis-unit-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "checkout"
        self.runner = self.base / "private-runner"
        self.repo.mkdir()
        self.runner.mkdir()
        self.payload = self.runner / "motionwright-uninstaller-123-1"
        self.observations = self.repo / "observations"

    def arguments(self):
        return dict(capture=gate.capture, repo=self.repo,
                    setup=self.repo / "fixture-setup.exe", observations=self.observations,
                    private_root=self.runner, private_payload=self.payload, context=context())

    def test_private_destination_is_bound_to_independent_runner_before_effects(self):
        self.assertEqual(static.private_layout(
            self.repo, self.runner, self.payload, context()), self.runner)
        for root, payload in ((self.repo, self.repo / self.payload.name),
                              (self.base, self.base / self.payload.name),
                              (self.runner, self.repo / self.payload.name),
                              (self.runner, self.runner / "different"),
                              (self.runner, Path(self.payload.name))):
            with self.subTest(root=str(root), payload=str(payload)), self.assertRaisesRegex(
                    ValueError, "nsis_static_private_destination"):
                static.private_layout(self.repo, root, payload, context())
        self.assertEqual(list(self.repo.iterdir()), [])
        self.assertEqual(list(self.runner.iterdir()), [])

    def test_reference_seal_is_required_before_reference_or_payload_access(self):
        for seal in (None, "", "0" * 63, "A" * 64):
            with self.subTest(seal=seal), self.assertRaisesRegex(
                    ValueError, "nsis_static_reference_seal_required"):
                static.verify(**self.arguments(), expected_reference_sha256=seal)
        self.assertFalse(self.observations.exists())
        self.assertFalse(self.payload.exists())

    def test_changed_seal_is_rejected_before_parsing_or_reconstructing(self):
        self.observations.mkdir()
        raw = b"not JSON; changed untrusted reference\n"
        (self.observations / "reference.json").write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "nsis_static_reference_seal_changed"):
            static.verify(**self.arguments(), expected_reference_sha256="0" * 64)
        self.assertEqual((self.observations / "reference.json").read_bytes(), raw)
        self.assertFalse(self.payload.exists())

    def test_setup_bytes_must_match_the_independent_context(self):
        setup = self.repo / "fixture-setup.exe"
        raw = b"inert original setup unit bytes"
        setup.write_bytes(raw)
        expected = context()
        expected["setup"] = {"filename": setup.name, **static.digest(raw)}
        self.assertEqual(static.source_identity(gate.capture, setup, expected), static.digest(raw))
        setup.write_bytes(raw[:-1] + b"!")
        with self.assertRaisesRegex(ValueError, "nsis_static_setup_changed"):
            static.source_identity(gate.capture, setup, expected)

    def test_reader_requires_pinned_executable_and_adjacent_library(self):
        directory = self.runner / "reader"
        directory.mkdir()
        reader = directory / "7z.exe"
        reader.write_bytes(b"inert executable unit input")
        (directory / "7z.dll").write_bytes(b"inert library unit input")
        with self.assertRaisesRegex(ValueError, "nsis_static_reader_pin"):
            static.reader_identity(gate.capture, reader)
        observed = static.reader_identity(gate.capture, reader, pinned=False)
        self.assertEqual(observed["executable"]["sha256"],
                         hashlib.sha256(reader.read_bytes()).hexdigest())
        self.assertEqual(observed["library"]["path"], str(directory / "7z.dll"))

    def test_preexisting_private_tool_intake_is_never_reused_or_overwritten(self):
        directory = self.runner / "motionwright-nsis-reader-123-1"
        directory.mkdir()
        original = directory / "keep"
        original.write_bytes(b"prior owner bytes")
        with self.assertRaisesRegex(ValueError, "nsis_reader_intake_exists"):
            static.provision_reader(capture=gate.capture, directory=directory,
                                    bootstrap=self.runner / "missing-7z.exe")
        self.assertEqual(original.read_bytes(), b"prior owner bytes")
        self.assertEqual({x.name for x in directory.iterdir()}, {"keep"})

    def test_inventory_seal_precedes_static_reference_consumption(self):
        path = self.repo / gate.INVENTORY
        path.parent.mkdir(parents=True)
        raw = b"changed inventory before any reference may be used"
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "windows_inventory_original_changed"):
            gate.verify_application_inventory(
                self.repo, "1" * 40, "123", self.runner / gate.INSTALL_DIRECTORY,
                "0" * 64, static_uninstaller=True)
        self.assertEqual(path.read_bytes(), raw)


class InstalledGeneratedMemberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mw-generated-member-unit-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.install = self.base / "installed"
        self.install.mkdir()
        self.main = b"inert main application unit bytes"
        self.uninstaller = b"inert independently retained generated-member unit bytes"
        (self.install / "motionwright-desktop.exe").write_bytes(self.main)
        # The reference is computed from separately retained bytes before copying.
        self.reference = {
            "expected_role": "INSTALLER_GENERATED_SELF",
            "reference_status": "ORIGINAL_SETUP_STATIC_RECONSTRUCTION",
            "expected_identity": static.digest(self.uninstaller),
            "setup_sha256": "1" * 64, "reference_sha256": "2" * 64,
        }
        (self.install / "uninstall.exe").write_bytes(self.uninstaller)

    def verify(self, reference):
        return verify_windows_application_files(
            capture=gate.capture, install_root=self.install,
            expected_files={"motionwright-desktop.exe": static.digest(self.main)},
            expected_uninstaller=reference)

    def test_reference_accepts_exact_bytes_while_legacy_keeps_generated_gap(self):
        result = self.verify(self.reference)
        self.assertTrue(result["all_member_bytes_authenticated"])
        self.assertTrue(result["namespace_closed"])
        self.assertEqual(result["full_package_inventory"], "NOT_VERIFIED")
        member = result["generated_members"]["uninstall.exe"]
        self.assertEqual(member["observed_identity"], self.reference["expected_identity"])
        self.assertEqual(member["reference_sha256"], "2" * 64)
        legacy = self.verify(None)
        self.assertFalse(legacy["all_member_bytes_authenticated"])
        self.assertEqual(legacy["generated_members"]["uninstall.exe"]["reference_status"],
                         "NOT_OBSERVED_BEFORE_PUBLICATION")

    def test_installed_digest_never_replaces_the_prior_expected_identity(self):
        prior = copy.deepcopy(self.reference)
        target = self.install / "uninstall.exe"
        target.write_bytes(self.uninstaller[:-1] + b"!")
        with self.assertRaisesRegex(ValueError, "candidate_generated_member_bytes_mismatch"):
            self.verify(self.reference)
        self.assertEqual(self.reference, prior)

    def test_invalid_generated_references_are_rejected(self):
        for key, value in (("reference_status", "INSTALLED_OBSERVATION"),
                           ("reference_sha256", "not-a-seal"), ("setup_sha256", "A" * 64),
                           ("expected_role", "REGULAR_FILE"), ("extra", 1)):
            changed = {**self.reference, key: value}
            with self.subTest(field=key), self.assertRaisesRegex(
                    ValueError, "candidate_generated_reference_invalid"):
                self.verify(changed)
        for identity in ({"bytes": True, "sha256": "1" * 64},
                         {"bytes": static.MAX_SELF + 1, "sha256": "1" * 64},
                         {"bytes": 1, "sha256": "unknown"}):
            with self.subTest(identity=identity), self.assertRaisesRegex(
                    ValueError, "candidate_generated_reference_identity_invalid"):
                self.verify({**self.reference, "expected_identity": identity})

    def test_static_reference_does_not_relax_namespace_or_hardlink_checks(self):
        extra = self.install / "unlisted.dll"
        extra.write_bytes(b"unadmitted")
        with self.assertRaisesRegex(ValueError, "candidate_application_tree_extra"):
            self.verify(self.reference)
        extra.unlink()
        target = self.install / "uninstall.exe"
        original = self.base / "original"
        original.write_bytes(self.uninstaller)
        target.unlink()
        os.link(original, target)
        with self.assertRaisesRegex(ValueError, "candidate_application_hardlink"):
            self.verify(self.reference)


if __name__ == "__main__":
    unittest.main()
