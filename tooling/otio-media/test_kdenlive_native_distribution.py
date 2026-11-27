"""Source checks for admission of KDE's official installed-editor inputs."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kdenlive_native_distribution as admission


class NativeDistributionTests(unittest.TestCase):
    def environment(self):
        source = "a" * 40
        return {
            "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "seradotcom/motionwright", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "kdenlive-native-editor", "RUNNER_OS": "Windows", "RUNNER_ARCH": "X64",
            "GITHUB_RUN_ID": "123", "GITHUB_SHA": source, "GITHUB_WORKFLOW_SHA": source,
            "GITHUB_WORKFLOW_REF": "seradotcom/motionwright/.github/workflows/v05-otio-real-media.yml@refs/heads/integrate",
        }

    def test_admits_only_the_original_native_runner_and_platform(self):
        original = self.environment()
        context, windows = admission.selection(original, "win32", "AMD64")
        self.assertEqual(context["run_id"], "123")
        self.assertEqual(windows.filename, "kdenlive-26.08.3.exe")
        self.assertEqual(windows.sha256, "1f4027f287c3066887b1c112664b10c4e71a6772914fd5ea41074df429652186")
        mac_env = {**original, "RUNNER_OS": "macOS", "RUNNER_ARCH": "ARM64"}
        _, mac = admission.selection(mac_env, "darwin", "arm64")
        self.assertEqual(mac.filename, "kdenlive-26.08.3-arm64.dmg")
        self.assertEqual(mac.sha256, "22b385f1e2e5bf45c5bb6954bd58a97db34cead219a9a9ae6faa499f0096d71e")
        for changed in ({"RUNNER_ARCH": "ARM64"}, {"GITHUB_RUN_ATTEMPT": "2"},
                        {"GITHUB_EVENT_NAME": "pull_request"}, {"GITHUB_JOB": "source-contracts"},
                        {"GITHUB_WORKFLOW_SHA": "b" * 40}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                admission.selection({**original, **changed}, "win32", "AMD64")

    def test_bad_original_does_not_publish_admission_or_download_again(self):
        context, distribution = admission.selection(self.environment(), "win32", "AMD64")
        expected = b"expected fixture"
        distribution = replace(distribution, sha256=sha256(expected).hexdigest())
        with tempfile.TemporaryDirectory() as directory, patch.object(admission.subprocess, "run") as run:
            root = Path(directory).resolve()
            curl = root / "bin/curl.exe"

            def substitute(argv, **kwargs):
                self.assertEqual(argv[0], str(curl))
                self.assertEqual(argv[1], "--disable")
                self.assertEqual(argv[-1], distribution.url)
                self.assertEqual(kwargs["timeout"], 250)
                self.assertNotIn("--retry", argv)
                Path(argv[argv.index("--output") + 1]).write_bytes(b"replaced fixture")

            run.side_effect = substitute
            with self.assertRaisesRegex(ValueError, "distribution digest differs"):
                admission.acquire(root, curl, context, distribution)
            work = root / "kdenlive-native-editor-123-1"
            self.assertTrue((work / "download-intent.json").is_file())
            self.assertFalse((work / "distribution.json").exists())
            with self.assertRaises(FileExistsError):
                admission.acquire(root, curl, context, distribution)
            run.assert_called_once()

    def test_verified_original_reports_the_exact_archive_identity(self):
        context, distribution = admission.selection(self.environment(), "win32", "AMD64")
        original = b"expected fixture"
        distribution = replace(distribution, sha256=sha256(original).hexdigest())
        with tempfile.TemporaryDirectory() as directory, patch.object(admission.subprocess, "run") as run:
            root = Path(directory).resolve()

            def download(argv, **_kwargs):
                Path(argv[argv.index("--output") + 1]).write_bytes(original)

            run.side_effect = download
            receipt = admission.acquire(root, root / "curl", context, distribution)
            self.assertEqual(receipt["archive"], distribution.filename)
            self.assertEqual(receipt["original"], {"sha256": distribution.sha256, "bytes": len(original)})
            self.assertEqual(receipt["context"], context)
            self.assertEqual(receipt["editor_launches"], 0)
            self.assertEqual(receipt["native_render_calls"], 0)
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
