"""Exercise the source-to-installed-editor handoff without opening an editor."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kdenlive_native_distribution as distribution
import kdenlive_native_source as source


class NativeSourceTests(unittest.TestCase):
    def fixture(self, root):
        selected = distribution.DISTRIBUTIONS[("darwin", "arm64", "macOS", "ARM64")]
        context = {"source_sha": "a" * 40, "run_id": "123", "run_attempt": "1",
                   "runner_os": "macOS", "runner_arch": "ARM64"}
        owner = root / "kdenlive-native-editor-123-1"
        relative = "installed/kdenlive.app/Contents/MacOS/kdenlive"
        editor = owner / relative
        editor.parent.mkdir(parents=True)
        raw = b"original installed editor"
        editor.write_bytes(raw)
        receipt = {"schema": "motionwright.kdenlive-native-installation/1",
                   "context": context, "archive": {"bytes": 123, "sha256": selected.sha256},
                   "installation_root": str(owner), "relative_executable": relative,
                   "original_executable": {"bytes": len(raw), "sha256": sha256(raw).hexdigest(),
                                           "format": "MACHO64_arm64"}}
        distribution.publish(owner / "installation.json", receipt)
        return selected, context, owner, editor

    def test_same_job_editor_uses_the_existing_three_interval_source_producer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            selected, context, owner, editor = self.fixture(root)
            exporter = root / "otio_real_media_fixture"
            exporter.write_bytes(b"pinned Rust exporter")
            tools = {"ffmpeg": {"bytes": 1, "sha256": "b" * 64},
                     "ffprobe": {"bytes": 1, "sha256": "c" * 64}}
            capsule = owner / "otio-original"
            with patch.object(source, "fixture_executable", return_value=exporter), patch.object(
                source, "video_tools", return_value=tools,
            ), patch.object(source, "produce_original") as produce:
                def delegate(destination, **_kwargs):
                    self.assertTrue((owner / "otio-source-intent.json").is_file())
                    destination.mkdir()
                    (destination / "timeline.otio").write_bytes(b"original OTIO")
                    manifest = json.dumps({"otio_sha256": sha256(b"original OTIO").hexdigest()}).encode()
                    (destination / "bridge-manifest.json").write_bytes(manifest)
                    return {
                        "GitHub_original_source_sha": context["source_sha"],
                        "compiled_original_Rust_OTIO_exporter_sha256": sha256(exporter.read_bytes()).hexdigest(),
                        "original_portable_manifest_sha256": sha256(manifest).hexdigest(),
                    }

                produce.side_effect = delegate
                report = source.prepare_native_source(root, context, selected)
                produce.assert_called_once_with(
                    capsule, temp=root, exporter=exporter, audio_source=False,
                    evidence_root=owner / "otio-source-evidence",
                )
                self.assertEqual(report["editor_sha256"], sha256(editor.read_bytes()).hexdigest())
                self.assertEqual(report["timeline"]["sha256"], sha256(b"original OTIO").hexdigest())
                self.assertEqual(report["context"], context)
                self.assertTrue((owner / "otio-source-ready.json").exists())
                with self.assertRaises(FileExistsError):
                    source.prepare_native_source(root, context, selected)
                produce.assert_called_once()

    def test_changed_editor_and_foreign_run_stop_before_media_is_generated(self):
        for change in ("editor", "run"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                selected, context, owner, editor = self.fixture(root)
                if change == "editor":
                    editor.write_bytes(b"changed installed editor!")
                else:
                    receipt = owner / "installation.json"
                    text = receipt.read_text()
                    receipt.write_text(text.replace('"source_sha": "' + "a" * 40,
                                                    '"source_sha": "' + "b" * 40))
                with patch.object(source, "produce_original") as produce:
                    with self.assertRaisesRegex(ValueError, "installed Kdenlive"):
                        source.prepare_native_source(root, context, selected)
                    produce.assert_not_called()
                self.assertFalse((owner / "otio-source-intent.json").exists())

    def test_manifest_cannot_bind_a_different_timeline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            selected, context, owner, _ = self.fixture(root)
            exporter = root / "otio_real_media_fixture"
            exporter.write_bytes(b"Rust exporter")

            def delegate(destination, **_kwargs):
                destination.mkdir()
                (destination / "timeline.otio").write_bytes(b"replacement OTIO")
                manifest = json.dumps({"otio_sha256": sha256(b"original OTIO").hexdigest()}).encode()
                (destination / "bridge-manifest.json").write_bytes(manifest)
                return {
                    "GitHub_original_source_sha": context["source_sha"],
                    "compiled_original_Rust_OTIO_exporter_sha256": sha256(exporter.read_bytes()).hexdigest(),
                    "original_portable_manifest_sha256": sha256(manifest).hexdigest(),
                }

            with patch.object(source, "fixture_executable", return_value=exporter), patch.object(
                source, "video_tools", return_value={},
            ), patch.object(source, "produce_original", side_effect=delegate):
                with self.assertRaisesRegex(ValueError, "manifest"):
                    source.prepare_native_source(root, context, selected)
            self.assertFalse((owner / "otio-source-ready.json").exists())

    def test_fixture_executable_uses_the_native_windows_suffix(self):
        self.assertEqual(source.fixture_executable({"runner_os": "Windows"}).name,
                         "otio_real_media_fixture.exe")
        self.assertEqual(source.fixture_executable({"runner_os": "macOS"}).name,
                         "otio_real_media_fixture")


if __name__ == "__main__":
    unittest.main()
