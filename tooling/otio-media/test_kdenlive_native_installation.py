"""Platform-neutral refusal tests for the native editor installation handoff."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kdenlive_native_distribution as distribution
import kdenlive_native_installation as installation


class NativeInstallationTests(unittest.TestCase):
    def fixture(self, root, os="Windows"):
        family = "windows" if os == "Windows" else "macOS"
        filename = "kdenlive-26.08.3.exe" if os == "Windows" else "kdenlive-26.08.3-arm64.dmg"
        content = b"official definition test"
        chosen = distribution.Distribution(family, filename, sha256(content).hexdigest())
        context = {"source_sha": "a" * 40, "run_id": "123", "run_attempt": "1",
                   "runner_os": os, "runner_arch": "X64" if os == "Windows" else "ARM64"}
        with patch.object(distribution.subprocess, "run") as fetch:
            def download(argv, **kwargs):
                Path(argv[argv.index("--output") + 1]).write_bytes(content)
            fetch.side_effect = download
            distribution.acquire(root, root / "curl", context, chosen)
        return context, chosen, root / "kdenlive-native-editor-123-1"

    def test_holds_one_attempt_and_requires_the_admitted_binary_in_its_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context, chosen, owner = self.fixture(root)
            target = owner / "installed/bin/kdenlive.exe"
            header = bytearray(256)
            header[:2] = b"MZ"
            header[60:64] = (128).to_bytes(4, "little")
            header[128:134] = b"PE\0\0\x64\x86"
            with patch.object(installation, "install_windows") as launch:
                def observed(archive, parent, identity):
                    self.assertTrue((owner / "installation-intent.json").exists())
                    self.assertEqual(archive, owner / chosen.filename)
                    self.assertEqual(parent, owner)
                    self.assertEqual(identity["sha256"], chosen.sha256)
                    target.parent.mkdir(parents=True)
                    target.write_bytes(header)
                    return target
                launch.side_effect = observed
                report = installation.install(root, context, chosen)
                self.assertEqual(report["relative_executable"], "installed/bin/kdenlive.exe")
                self.assertEqual(report["original_executable"]["sha256"],
                                 sha256(header).hexdigest())
                self.assertEqual(report["original_executable"]["format"], "PE64_x86_64")
                with self.assertRaises(FileExistsError):
                    installation.install(root, context, chosen)
                launch.assert_called_once()

    def test_windows_launch_uses_the_current_process_identity_reader(self):
        import windows_updated_application as windows

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context, chosen, owner = self.fixture(root)
            archive = owner / chosen.filename
            original = installation.regular_file(archive, 1024)
            with patch.object(installation.subprocess, "Popen") as launch, patch.object(
                windows, "process_identity", return_value={"pid": 42, "creation_filetime": 8},
            ) as observe:
                launch.return_value.wait.return_value = 0
                executable = installation.install_windows(archive, owner, original)
            observe.assert_called_once_with(launch.return_value, archive)
            self.assertEqual(executable, owner / "installed/bin/kdenlive.exe")
            self.assertTrue((owner / "windows-installer-terminal.json").exists())
            launch.assert_called_once()

    def test_macos_mounts_a_byte_identical_copy_and_preserves_the_archive(self):
        import ci_macos_package_metadata as mac
        import native_host_installed_shared_state as host
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context, chosen, owner = self.fixture(root, "macOS")
            archive = owner / chosen.filename
            original = installation.regular_file(archive, 1024)
            mount = owner / "mount"

            def copy_image(src, copy, maximum, *, expected):
                self.assertEqual((src, expected), (archive, original))
                copy.write_bytes(src.read_bytes())

            def run(arguments, **options):
                if arguments[:2] == ["/usr/bin/hdiutil", "attach"]:
                    image = Path(arguments[-1])
                    self.assertNotEqual(image, archive)
                    self.assertEqual(image.read_bytes(), archive.read_bytes())
                    (mount / "Kdenlive.app").mkdir()
                    return SimpleNamespace(stdout=b"metadata")
                return SimpleNamespace()

            def bundle(root):
                return root / "Contents/MacOS/kdenlive", {"fixture": "same bundle"}

            with patch.object(host, "ci_copy_original", side_effect=copy_image), patch.object(
                installation.subprocess, "run", side_effect=run,
            ), patch.object(mac, "mounted_device", return_value="/dev/disk1"), patch.object(
                mac, "readonly_mount_identity", return_value={"read_only": True},
            ), patch.object(installation, "bundle_identity", side_effect=bundle):
                executable = installation.install_macos(archive, owner, original)
            self.assertEqual(executable, owner / "installed/Kdenlive.app/Contents/MacOS/kdenlive")
            self.assertEqual(installation.regular_file(archive, 1024), original)

    def test_a_changed_archive_never_reaches_the_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context, chosen, owner = self.fixture(root)
            archive = owner / chosen.filename
            archive.write_bytes(b"x" * archive.stat().st_size)
            with patch.object(installation, "install_windows") as launch:
                with self.assertRaisesRegex(ValueError, "archive"):
                    installation.install(root, context, chosen)
                launch.assert_not_called()
                self.assertFalse((owner / "installation-intent.json").exists())

    def test_installed_binary_must_be_native_for_the_selected_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context, chosen, owner = self.fixture(root)
            target = owner / "installed/bin/kdenlive.exe"
            target.parent.mkdir(parents=True)
            target.write_bytes(bytes(256))
            with patch.object(installation, "install_windows", return_value=target):
                with self.assertRaisesRegex(ValueError, "MZ header"):
                    installation.install(root, context, chosen)
                self.assertFalse((owner / "installation.json").exists())

    def test_a_foreign_binary_is_never_published_even_if_the_installer_calls_it_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            context, chosen, owner = self.fixture(root, "macOS")
            foreign = root / "kdenlive"
            foreign.write_bytes(b"unbound editor")
            with patch.object(installation, "install_macos", return_value=foreign):
                with self.assertRaisesRegex(ValueError, "outside"):
                    installation.install(root, context, chosen)
                self.assertFalse((owner / "installation.json").exists())


if __name__ == "__main__":
    unittest.main()
