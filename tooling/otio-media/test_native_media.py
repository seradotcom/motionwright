#!/usr/bin/env python3
"""Small original H.264 regressions using real FFmpeg/FFprobe, without an NLE.

These tests exercise media admission and portable source preservation. They do
not certify external editor acceptance, source rights or human creative review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from bridge import BridgeRefused, SCHEMA, export_portable, file_probe, import_preview, trange


def source_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(value: int) -> str:
    return f"00000000-0000-4000-8000-{value:012x}"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                     "Real media admission tests require FFmpeg and FFprobe")
class NativeMediaAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory(prefix="mw-otio-native-fixture-")
        cls.addClassCleanup(cls.fixture_temp.cleanup)
        original = Path(cls.fixture_temp.name) / "original.mp4"
        result = subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi",
            "-i", "testsrc2=size=160x90:rate=30:duration=2",
            "-frames:v", "60", "-an", "-c:v", "libx264", "-threads", "1",
            "-preset", "ultrafast", "-crf", "25", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(original),
        ], capture_output=True, timeout=30, check=False)
        if result.returncode:
            raise AssertionError("Original bounded H.264 fixture generation failed")
        cls.original_bytes = original.read_bytes()
        cls.truncated_bytes = cls.original_bytes[:len(cls.original_bytes) * 2 // 3]
        if len(cls.original_bytes) < 1000:
            raise AssertionError("Original H.264 fixture is unexpectedly small")

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="mw-otio-native-admission-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.owner = self.root / "owner"
        self.owner.mkdir()
        self.output = self.root / "portable"

    def owner_inputs(self, data: bytes) -> dict:
        digest = source_sha(data)
        (self.owner / "original.mp4").write_bytes(data)
        self.project = {
            "id": identity(1), "generation": identity(2), "revision": 7,
            "title": "Original synthetic media admission fixture",
            "scenes": [{"id": identity(10), "name": "Original two-second scene",
                        "start": {"num": "0", "den": "1"},
                        "duration": {"num": "2", "den": "1"}}],
            "assets": [{"id": identity(50), "name": "Original generated source",
                        "media_type": "video/mp4", "content_sha256": digest,
                        "source_revision": "synthetic-native-admission/1"}],
        }
        otio = {
            "OTIO_SCHEMA": "Timeline.1", "name": self.project["title"],
            "metadata": {"motionwright": {
                "project_id": self.project["id"],
                "generation": self.project["generation"], "revision": 7,
                "export_profile": "conservative-cut-v1",
                "loss_report": ["Original synthetic fixture; no human approval."],
            }},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "children": [{
                "OTIO_SCHEMA": "Track.1", "kind": "Video", "children": [{
                    "OTIO_SCHEMA": "Clip.1", "name": "Original two-second scene",
                    "enabled": True, "effects": [], "markers": [],
                    "media_reference": {"OTIO_SCHEMA": "MissingReference.1",
                                        "available_range": None, "metadata": {},
                                        "name": None},
                    "source_range": trange(0, 48, 24),
                    "metadata": {"motionwright": {"scene_id": identity(10)}},
                }],
            }]},
        }
        (self.owner / "project.json").write_text(json.dumps(self.project), encoding="utf8")
        (self.owner / "source.otio").write_text(json.dumps(otio), encoding="utf8")
        return {
            "schema": SCHEMA, "project": "project.json", "otio": "source.otio",
            "bindings": [{
                "scene_id": identity(10), "asset_id": identity(50),
                "file": "original.mp4", "sha256": digest, "source_start_frame": 0,
                "rights": {"owner": "Original synthetic fixture",
                           "license": "Original synthetic generated test media",
                           "authorized": True, "redistributable": True},
            }],
            "owner_review": {"reviewer": "Synthetic test; not human acceptance",
                             "synthetic_source_allowed": True},
        }

    def test_complete_original_video_exports_and_imports_without_changing_bytes(self):
        request = self.owner_inputs(self.original_bytes)
        self.assertEqual(file_probe(self.owner / "original.mp4"), {
            "fps": 30, "frames": 60, "width": 160, "height": 90, "codec": "h264",
        })
        report = export_portable(self.owner, request, self.output)
        self.assertEqual(report["verified_media_refs"], 1)
        self.assertFalse(report["owner_publication_authorized"])
        digest = source_sha(self.original_bytes)
        self.assertEqual((self.output / "media" / f"{digest}.mp4").read_bytes(),
                         self.original_bytes)
        imported = import_preview(self.output, "timeline.otio",
                                  self.output / "source-project.json")
        self.assertEqual(imported["media_refs_verified"], 1)
        self.assertFalse(imported["edit_imported"])
        self.assertEqual(imported["native_change_proposals"], [])

    def test_faststart_truncation_is_refused_with_intact_stream_metadata(self):
        self.owner_inputs(self.truncated_bytes)
        media = self.owner / "original.mp4"
        header = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=codec_name,nb_frames,r_frame_rate,avg_frame_rate", "-of", "json",
            str(media),
        ], capture_output=True, timeout=20, check=False)
        self.assertEqual(header.returncode, 0)
        stream = json.loads(header.stdout)["streams"][0]
        self.assertEqual(stream["nb_frames"], "60")
        self.assertEqual(stream["r_frame_rate"], "30/1")
        self.assertEqual(stream["avg_frame_rate"], "30/1")
        with self.assertRaises(BridgeRefused):
            file_probe(media)

    def test_corrupt_source_is_refused_before_export_destination_exists(self):
        request = self.owner_inputs(self.truncated_bytes)
        with self.assertRaises(BridgeRefused):
            export_portable(self.owner, request, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual((self.owner / "original.mp4").read_bytes(), self.truncated_bytes)

    def test_hash_matched_legacy_corrupt_bundle_is_refused_on_import(self):
        request = self.owner_inputs(self.original_bytes)
        export_portable(self.owner, request, self.output)
        # Model a legacy bundle that admitted corrupt source bytes with their
        # correct SHA. All identity checks remain internally consistent, so
        # import must reject the unreadable frames rather than rely on hashes.
        old_digest = source_sha(self.original_bytes)
        digest = source_sha(self.truncated_bytes)
        (self.output / "media" / f"{old_digest}.mp4").unlink()
        (self.output / "media" / f"{digest}.mp4").write_bytes(self.truncated_bytes)
        self.project["assets"][0]["content_sha256"] = digest
        project_bytes = json.dumps(self.project).encode()
        project_digest = source_sha(project_bytes)
        (self.output / "source-project.json").write_bytes(project_bytes)

        manifest_path = self.output / "bridge-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        manifest["project_sha256"] = project_digest
        bound = manifest["bound_media"][0]
        bound.update(source_sha256=digest, path=f"media/{digest}.mp4",
                     bytes=len(self.truncated_bytes))

        timeline_path = self.output / "timeline.otio"
        timeline = json.loads(timeline_path.read_text(encoding="utf8"))
        timeline["metadata"]["motionwright"]["source_project_sha256"] = project_digest
        clip = timeline["tracks"]["children"][0]["children"][0]
        clip["metadata"]["motionwright"]["media_sha256"] = digest
        clip["media_reference"]["target_url"] = bound["path"]
        clip["media_reference"]["metadata"]["motionwright"]["asset_sha256"] = digest
        timeline_bytes = json.dumps(timeline).encode()
        timeline_path.write_bytes(timeline_bytes)
        manifest["otio_sha256"] = source_sha(timeline_bytes)
        manifest_path.write_text(json.dumps(manifest), encoding="utf8")
        with self.assertRaises(BridgeRefused):
            import_preview(self.output, "timeline.otio", self.output / "source-project.json")


if __name__ == "__main__":
    unittest.main()
