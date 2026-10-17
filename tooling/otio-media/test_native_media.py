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
import sys
import tempfile
import unittest

from bridge import (
    BridgeRefused, SCHEMA, export_portable, file_probe, import_preview, trange,
    preview_kdenlive_cut, KDENLIVE_REQUEST_SCHEMA,
)


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

    def bounded_raw_inputs(self):
        """Use the real source video with explicit RAW-shaped parser test data.

        No Kdenlive process, native receiver receipt or final NLE frame oracle
        is created here. Only the admitted source video is physically decoded.
        """
        request = self.owner_inputs(self.original_bytes)
        export_portable(self.owner, request, self.output)
        raw_root = self.root / "raw"
        raw_root.mkdir()
        original = json.loads((self.output / "timeline.otio").read_text())
        original_clip = original["tracks"]["children"][0]["children"][0]
        reference = original_clip["media_reference"]
        reference["metadata"] = {}
        reference["name"] = ""
        reference["available_image_bounds"] = None
        reference["target_url"] = str(self.output / reference["target_url"])
        clip = {"OTIO_SCHEMA": "Clip.2", "name": "source-native-name.mp4",
                "metadata": {}, "enabled": True, "effects": [], "markers": [],
                "source_range": original_clip["source_range"],
                "media_references": {"DEFAULT_MEDIA": reference},
                "active_media_reference_key": "DEFAULT_MEDIA"}
        raw = {"OTIO_SCHEMA": "Timeline.1", "name": "", "metadata": {},
               "global_start_time": None,
               "tracks": {"OTIO_SCHEMA": "Stack.1", "metadata": {},
                          "source_range": None, "effects": [], "markers": [],
                          "children": [{"OTIO_SCHEMA": "Track.1", "kind": "Video",
                                        "name": "V1", "metadata": {}, "effects": [],
                                        "markers": [], "enabled": True,
                                        "source_range": trange(0, 60, 30), "children": [clip]}]}}
        (raw_root / "received.otio").write_text(json.dumps(raw), encoding="utf8")
        (raw_root / "edited.otio").write_text(json.dumps(raw), encoding="utf8")
        return raw_root, raw

    def bounded_request(self, raw_root):
        return {"schema": KDENLIVE_REQUEST_SCHEMA,
                "capsule_manifest_sha256": source_sha((self.output / "bridge-manifest.json").read_bytes()),
                "received_otio": "received.otio",
                "received_sha256": source_sha((raw_root / "received.otio").read_bytes()),
                "edited_otio": "edited.otio",
                "edited_sha256": source_sha((raw_root / "edited.otio").read_bytes())}

    def test_bound_native_cut_reads_real_source_and_preserves_every_raw_and_capsule_byte(self):
        raw_root, edited = self.bounded_raw_inputs()
        track = edited["tracks"]["children"][0]
        track["source_range"] = trange(0, 30, 30)
        track["children"][0]["source_range"] = trange(0, 30, 30)
        (raw_root / "edited.otio").write_text(json.dumps(edited), encoding="utf8")
        paths = [self.output / "bridge-manifest.json", self.output / "timeline.otio",
                 self.output / "source-project.json", raw_root / "received.otio",
                 raw_root / "edited.otio", self.output / "media" / (source_sha(self.original_bytes) + ".mp4")]
        before = {path: path.read_bytes() for path in paths}
        result = preview_kdenlive_cut(self.output, raw_root, self.bounded_request(raw_root),
            self.output / "source-project.json", authorized_media_roots=(self.output, raw_root))
        self.assertEqual(result["status"], "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED")
        self.assertEqual(result["native_change_proposals"], [{"type": "set_scene_duration",
            "scene_id": identity(10), "duration": {"num": "1", "den": "1"}}])
        self.assertEqual(result["source_identity_mapping"][0]["source_sha256"], source_sha(self.original_bytes))
        self.assertFalse(result["native_final_frames_verified"])
        self.assertEqual(result["nle_receiver_acceptance"], "NOT_VERIFIED")
        self.assertEqual(result["native_project_reopened"], "NOT_VERIFIED")
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_cli_same_byte_relink_and_missing_clip_return_explicit_distinct_statuses(self):
        raw_root, edited = self.bounded_raw_inputs()
        relocated = raw_root / "relocated.mp4"
        relocated.write_bytes(self.original_bytes)
        edited["tracks"]["children"][0]["children"][0]["media_references"]["DEFAULT_MEDIA"]["target_url"] = str(relocated)
        (raw_root / "edited.otio").write_text(json.dumps(edited), encoding="utf8")
        request = raw_root / "request.json"
        request.write_text(json.dumps(self.bounded_request(raw_root)), encoding="utf8")
        output = raw_root / "proposal.json"
        command = [sys.executable, "-B", str(Path(__file__).with_name("bridge.py")),
                   "preview-kdenlive-cut", "--bundle", str(self.output),
                   "--raw-root", str(raw_root), "--request", "request.json",
                   "--current-project", str(self.output / "source-project.json"),
                   "--media-root", str(self.output), "--media-root", str(raw_root),
                   "--output", str(output)]
        observed = subprocess.run(command, capture_output=True, timeout=30, check=False)
        self.assertEqual(observed.returncode, 0, observed.stderr.decode())
        result = json.loads(output.read_text())
        self.assertTrue(result["same_source_bytes_relocated"])
        self.assertEqual(result["native_change_proposals"], [])
        self.assertFalse(result["source_media_relinked"])
        self.assertFalse(result["native_final_frames_verified"])
        output_bytes = output.read_bytes()
        duplicate = subprocess.run(command, capture_output=True, timeout=30, check=False)
        self.assertEqual(duplicate.returncode, 2)
        self.assertEqual(output.read_bytes(), output_bytes)
        edited["tracks"]["children"][0]["children"] = []
        edited["tracks"]["children"][0]["source_range"] = None
        # Preserve the successful RAW and request; the negative is a new input.
        omitted = raw_root / "edited-omitted.otio"
        omitted.write_text(json.dumps(edited), encoding="utf8")
        loss_request = {**self.bounded_request(raw_root), "edited_otio": omitted.name,
                        "edited_sha256": source_sha(omitted.read_bytes())}
        (raw_root / "request-loss.json").write_text(json.dumps(loss_request), encoding="utf8")
        loss_output = raw_root / "rejected-loss.json"
        loss_command = command[:-1] + [str(loss_output)]
        loss_command[loss_command.index("--request") + 1] = "request-loss.json"
        observed = subprocess.run(loss_command, capture_output=True,
                                  timeout=30, check=False)
        self.assertEqual(observed.returncode, 3, observed.stderr.decode())
        loss = json.loads(loss_output.read_text())
        self.assertEqual(loss["status"], "REJECTED_LOSS")
        self.assertEqual(loss["native_change_proposals"], [])
        self.assertFalse(loss["edit_imported"])
        self.assertEqual(loss["nle_receiver_acceptance"], "NOT_VERIFIED")

    def test_foreign_or_truncated_relink_bytes_cannot_use_the_original_media_name(self):
        raw_root, edited = self.bounded_raw_inputs()
        original_name = source_sha(self.original_bytes) + ".mp4"
        foreign = raw_root / original_name
        foreign.write_bytes(self.truncated_bytes)
        edited["tracks"]["children"][0]["children"][0]["media_references"]["DEFAULT_MEDIA"]["target_url"] = str(foreign)
        (raw_root / "edited.otio").write_text(json.dumps(edited), encoding="utf8")
        result = preview_kdenlive_cut(self.output, raw_root, self.bounded_request(raw_root),
            self.output / "source-project.json", authorized_media_roots=(self.output, raw_root))
        self.assertEqual(result["status"], "REJECTED_LOSS")
        self.assertEqual(result["rejection_code"], "unbound_source_or_inpoint")
        self.assertEqual(result["native_change_proposals"], [])
        self.assertEqual(foreign.read_bytes(), self.truncated_bytes)
        self.assertEqual((self.output / "media" / original_name).read_bytes(), self.original_bytes)


if __name__ == "__main__":
    unittest.main()
