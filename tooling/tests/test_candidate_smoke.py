"""Adversarial tests for source-bound inspection of unsigned OS candidates."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import plistlib
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "candidate_smoke", ROOT / "tooling" / "candidate_smoke.py"
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def elf_binary() -> bytes:
    data = bytearray(512)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, 62)
    return bytes(data)


def pe_binary() -> bytes:
    data = bytearray(512)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", data, 0x84, 0x8664)
    return bytes(data)


def macho_binary() -> bytes:
    data = bytearray(512)
    data[:4] = b"\xcf\xfa\xed\xfe"
    struct.pack_into("<I", data, 4, 0x0100000c)
    return bytes(data)


class CandidateSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.sha = "a" * 40
        lock = json.loads((ROOT / "SOURCE_LOCK.json").read_text())
        self.semwright = lock["dependencies"]["semwright"]["revision"]

    def receipt(self, kind: str, payload: bytes, arch: str) -> tuple[Path, Path]:
        platform = mod.METHODS[kind][0]
        artifact = self.folder / {
            "appimage": "Motionwright_0.1.0_amd64.AppImage",
            "deb": "Motionwright_0.1.0_amd64.deb",
            "nsis": "Motionwright_0.1.0_x64-setup.exe",
            "dmg": "Motionwright_0.1.0_arm64.dmg",
        }[kind]
        artifact.write_bytes(payload)
        config = json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text())
        content = {
            "schema_version": 1,
            "candidate": {
                "application": "Motionwright", "version": config["version"],
                "source_revision": self.sha, "semwright_revision": self.semwright,
                "platform": platform, "arch": arch,
            },
            "artifacts": [{
                "filename": artifact.name,
                "bundle_kind": kind, "bytes": artifact.stat().st_size,
                "sha256": mod.hash_file(artifact),
            }],
            "claims": {
                "published": False, "human_install_acceptance": "NOT_RUN",
                "distribution_signature": "NOT_CLAIMED",
                "notarization": "NOT_CLAIMED",
            },
        }
        receipt_path = self.folder / "receipt.json"
        receipt_path.write_text(json.dumps(content))
        return receipt_path, artifact

    def test_valid_debian_extract_with_matching_desktop_entry(self):
        receipt, artifact = self.receipt("deb", b"fake-debian-candidate", "x86_64")
        binary = self.folder / "motionwright-desktop"
        binary.write_bytes(elf_binary())
        entry = self.folder / "com.seradotcom.motionwright.desktop"
        entry.write_text("[Desktop Entry]\nName=Motionwright\nType=Application\nExec=motionwright-desktop\n")
        data = mod.build_receipt(
            candidate_receipt=receipt, artifact=artifact, installed_binary=binary,
            kind="deb", method="deb_extract", linux_desktop_entry=entry
        )
        self.assertEqual(data["automated_inspection"]["binary"]["binary_format"], "ELF64_x86_64")
        self.assertEqual(data["automated_inspection"]["desktop_exec"], binary.name)
        self.assertEqual(data["claims"]["human_install_acceptance"], "NOT_RUN")
        self.assertFalse(data["claims"]["release_published"])

    def test_appimage_gui_claim_requires_real_window_evidence(self):
        receipt, artifact = self.receipt("appimage", b"fake-appimage-candidate", "x86_64")
        binary = self.folder / "motionwright-desktop"
        binary.write_bytes(elf_binary())
        entry = self.folder / "app.desktop"
        entry.write_text("[Desktop Entry]\nName=Motionwright\nType=Application\nExec=motionwright-desktop\n")
        with self.assertRaisesRegex(mod.CandidateError, "does not prove"):
            mod.build_receipt(
                candidate_receipt=receipt, artifact=artifact, installed_binary=binary,
                kind="appimage", method="appimage_extract", linux_desktop_entry=entry,
                linux_gui_probe={"status": "PROCESS_STARTED", "window_title": ""},
            )
        passed = mod.build_receipt(
            candidate_receipt=receipt, artifact=artifact, installed_binary=binary,
            kind="appimage", method="appimage_extract", linux_desktop_entry=entry,
            linux_gui_probe={"status": "WINDOW_OBSERVED", "window_title": "Motionwright"},
        )
        self.assertEqual(passed["automated_inspection"]["gui_window_probe"], "WINDOW_OBSERVED")

    def test_appimage_internal_desktop_symlink_is_allowed_but_escape_is_not(self):
        # Linuxdeploy creates a top-level symlink to usr/share/applications.
        # The legitimate relative link must not be confused with a host-path
        # escape, and Debian's extracted entry must remain a regular file.
        root = self.folder / "squashfs-root"
        target_dir = root / "usr" / "share" / "applications"
        target_dir.mkdir(parents=True)
        executable = root / "usr" / "bin" / "motionwright-desktop"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(elf_binary())
        target = target_dir / "Motionwright.desktop"
        target.write_text(
            "[Desktop Entry]\nName=Motionwright\nType=Application\nExec=motionwright-desktop\n"
        )
        entry = root / "Motionwright.desktop"
        entry.symlink_to("usr/share/applications/Motionwright.desktop")
        data = mod.verify_linux_desktop_entry(
            entry, executable, allow_internal_link=True
        )
        self.assertEqual(data["desktop_entry_filename"], "Motionwright.desktop")
        with self.assertRaisesRegex(mod.CandidateError, "may not be a symlink"):
            mod.verify_linux_desktop_entry(entry, executable)

        entry.unlink()
        outsider = self.folder / "outside.desktop"
        outsider.write_bytes(target.read_bytes())
        entry.symlink_to(outsider)
        with self.assertRaisesRegex(mod.CandidateError, "escapes the extracted bundle"):
            mod.verify_linux_desktop_entry(entry, executable, allow_internal_link=True)

    def test_windows_silent_current_user_installer_candidate(self):
        receipt, artifact = self.receipt("nsis", b"fake-windows-installer", "x86_64")
        executable = self.folder / "Motionwright.exe"
        executable.write_bytes(pe_binary())
        data = mod.build_receipt(
            candidate_receipt=receipt, artifact=artifact, installed_binary=executable,
            kind="nsis", method="nsis_silent_current_user"
        )
        self.assertEqual(data["automated_inspection"]["binary"]["binary_format"], "PE64_x86_64")

    def test_macos_readonly_dmg_copy_checks_bundle_identifier_and_machine(self):
        receipt, artifact = self.receipt("dmg", b"fake-dmg-installer", "arm64")
        executable = self.folder / "Motionwright"
        executable.write_bytes(macho_binary())
        info = self.folder / "Info.plist"
        with info.open("wb") as out:
            plistlib.dump({
                "CFBundleIdentifier": "com.seradotcom.motionwright",
                "CFBundleExecutable": executable.name,
            }, out)
        data = mod.build_receipt(
            candidate_receipt=receipt, artifact=artifact, installed_binary=executable,
            kind="dmg", method="dmg_readonly_mount_copy", mac_info_plist=info
        )
        self.assertEqual(data["automated_inspection"]["bundle_identifier"],
                         "com.seradotcom.motionwright")
        with info.open("wb") as out:
            plistlib.dump({"CFBundleIdentifier": "fake", "CFBundleExecutable": executable.name}, out)
        with self.assertRaisesRegex(mod.CandidateError, "identifier mismatch"):
            mod.build_receipt(
                candidate_receipt=receipt, artifact=artifact, installed_binary=executable,
                kind="dmg", method="dmg_readonly_mount_copy", mac_info_plist=info
            )

    def test_modified_or_wrong_source_archive_fails_closed(self):
        receipt, artifact = self.receipt("deb", b"fake-debian-candidate", "x86_64")
        artifact.write_bytes(b"tampered same-size payload?")
        with self.assertRaisesRegex(mod.CandidateError, "byte count|bytes changed"):
            mod.verify_installer(json.loads(receipt.read_text()), "deb", artifact)
        artifact.write_bytes(b"fake-debian-candidate")
        doc = json.loads(receipt.read_text())
        doc["candidate"]["semwright_revision"] = "f" * 40
        with self.assertRaisesRegex(mod.CandidateError, "pinned Semwright"):
            mod.verify_installer(doc, "deb", artifact)
        doc["candidate"]["semwright_revision"] = self.semwright
        doc["claims"]["human_install_acceptance"] = "PASS"
        with self.assertRaisesRegex(mod.CandidateError, "improperly claims"):
            mod.verify_installer(doc, "deb", artifact)

    def test_cross_platform_binary_and_method_substitution_fails(self):
        receipt, artifact = self.receipt("deb", b"candidate", "x86_64")
        binary = self.folder / "motionwright"
        binary.write_bytes(pe_binary())
        with self.assertRaisesRegex(mod.CandidateError, "ELF"):
            mod.binary_identity(binary, "linux", "x86_64")
        binary.write_bytes(elf_binary())
        entry = self.folder / "entry.desktop"
        entry.write_text("[Desktop Entry]\nName=Motionwright\nType=Application\nExec=not-the-product\n")
        with self.assertRaisesRegex(mod.CandidateError, "different executable"):
            mod.build_receipt(
                candidate_receipt=receipt, artifact=artifact, installed_binary=binary,
                kind="deb", method="deb_extract", linux_desktop_entry=entry
            )
        with self.assertRaisesRegex(mod.CandidateError, "method does not match"):
            mod.build_receipt(
                candidate_receipt=receipt, artifact=artifact, installed_binary=binary,
                kind="deb", method="nsis_silent_current_user"
            )

    def test_symlink_substitution_is_rejected(self):
        receipt, artifact = self.receipt("nsis", b"candidate", "x86_64")
        binary = self.folder / "source.exe"
        binary.write_bytes(pe_binary())
        symlink = self.folder / "Motionwright.exe"
        symlink.symlink_to(binary)
        with self.assertRaisesRegex(mod.CandidateError, "non-symlink"):
            mod.build_receipt(
                candidate_receipt=receipt, artifact=artifact, installed_binary=symlink,
                kind="nsis", method="nsis_silent_current_user"
            )

    def test_gui_title_parser_requires_actual_client_label(self):
        gui = importlib.util.spec_from_file_location(
            "linux_gui_probe", ROOT / "tooling" / "linux_gui_probe.py"
        )
        assert gui and gui.loader
        module = importlib.util.module_from_spec(gui)
        gui.loader.exec_module(module)
        self.assertTrue(module.check_window_output('0x123 "Motionwright": ("Motionwright")'))
        self.assertFalse(module.check_window_output('0x124 "Other Application": ("Other")'))


if __name__ == "__main__":
    unittest.main()
