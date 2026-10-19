"""Source-specific negative controls; never infer physical render from unit tests."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_film180_media_readback as verifier

UUIDS = [
    f"00000000-0000-4000-8000-{i:012x}" for i in range(11, 21)
]


def original_source_and_receipts() -> tuple[dict, dict, dict]:
    project, generation, profile, scene1, scene2, voice = UUIDS[:6]
    nodes = UUIDS[6:10]
    sha = "ab" * 32
    source = {
        "id": project, "generation": generation, "revision": 11,
        "deliverables": [{
            "id": profile, "width": 1920, "height": 1080,
            "captions": True, "burn_in_captions": True,
            "voice_track_id": voice,
        }],
        "scenes": [
            {"id": scene1, "nodes": [{"id": i} for i in nodes[:3]]},
            {"id": scene2, "nodes": [{"id": nodes[3]}]},
        ],
        "audio": {"voice_tracks": [{
            "id": voice, "source_sha256": sha, "sample_rate_hz": 48000,
            "channels": 2,
        }]},
    }
    receipt = {
        "project_resource": f"project:{project}", "generation": generation,
        "revision": 11, "deliverable_id": profile,
        "frame_rate": {"num": 30, "den": 1},
        "segments": [{
            "segment_id": "segment-0", "frame_count": 180,
            "scene_ids": [scene1, scene2],
            "verification": {"report": {"support_level": "native"}},
        }],
    }
    master = {
        "project_resource": f"project:{project}", "generation": generation,
        "revision": 11, "deliverable_id": profile, "motion_segment_id": "segment-0",
        "frame_rate": {"num": 30, "den": 1}, "frame_count": 180,
        "source_audio": {
            "sha256": sha, "sample_rate": 48000, "channels": 2,
        },
        "master": {
            "profile": "h264-aac-mp4", "artifact": {
                "root": "output", "sha256": "cd" * 32,
            },
        },
    }
    return source, receipt, master


def pixels(width: int = 72, height: int = 72,
           *, missing_caption: bool = False, no_cut: bool = False,
           same_captions: bool = False) -> dict[int, bytes]:
    out = {}
    for frame in verifier.SELECTED:
        is_second = frame >= 90
        body = bytearray(bytes([22, 36, 55]) * (width * height))
        if is_second and not no_cut:
            body = bytearray(bytes([68, 84, 96]) * (width * height))
        if not missing_caption and not is_second:
            which = 1 if 6 <= frame < 27 else 2 if 33 <= frame < 72 else 0
            if which:
                x0 = 10 if which == 1 or same_captions else 36
                for y in range(50, 59):
                    for x in range(x0, x0 + 9):
                        at = (y * width + x) * 3
                        body[at:at + 3] = b"\xff\xfe\xf0"
        out[frame] = bytes(body)
    return out


def probe() -> dict:
    return {
        "streams": [
            {
                "codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
            },
            {
                "codec_type": "audio", "codec_name": "aac",
                "sample_rate": "48000", "channels": 2,
            },
        ],
        "frames": [
            {"media_type": "video", "best_effort_timestamp_time": f"{i/30:.6f}"}
            for i in range(180)
        ],
    }


class Film180ReadbackContracts(unittest.TestCase):
    def test_source_custody_requires_original_native_receipt_and_media_hash(self):
        src, film, master = original_source_and_receipts()
        out = verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)
        self.assertEqual(len(out["scene_ids"]), 2)
        self.assertEqual(len(out["node_ids"]), 4)
        self.assertEqual(out["revision"], 11)

    def test_forged_old_film_revision_is_rejected(self):
        src, film, master = original_source_and_receipts()
        film["revision"] = 10
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "revision"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_forged_audio_digest_is_not_original_source(self):
        src, film, master = original_source_and_receipts()
        master["source_audio"]["sha256"] = "ef" * 32
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "voice"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_original_media_cannot_be_unbound_to_native_result(self):
        src, film, master = original_source_and_receipts()
        master["master"]["artifact"]["sha256"] = "ef" * 32
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "master_artifact"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_original_native_180_video_pts_are_exact(self):
        out = verifier.inspect_video_probe(probe())
        self.assertEqual(out["video_frames_decoded"], 180)
        self.assertAlmostEqual(out["last_pts"], 179 / 30, places=5)

    def test_video_pts_duplicate_frame_or_wrong_codec_rejected(self):
        for alteration in ("duplicate", "codec", "skip"):
            data = probe()
            if alteration == "duplicate":
                data["frames"][89]["best_effort_timestamp_time"] = "2.933333"
            elif alteration == "skip":
                data["frames"].pop(50)
            else:
                data["streams"][0]["codec_name"] = "mpeg4"
            with self.subTest(alteration=alteration):
                with self.assertRaises(verifier.OriginalMediaMismatch):
                    verifier.inspect_video_probe(data)

    def test_caption_and_scene_boundary_pixels_positive_control(self):
        result = verifier.inspect_selected_pixels(pixels(), 72, 72)
        self.assertEqual(result["actual_selected_frame_count"], 12)
        self.assertGreater(result["scene_cut_89_to_90_changed_pixels"], 50)

    def test_frozen_caption_frames_cannot_pass_with_a_valid_180_frame_count(self):
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "caption_boundary_missing"):
            verifier.inspect_selected_pixels(pixels(missing_caption=True), 72, 72)

    def test_identical_source_scenes_cannot_pass_as_a_real_editable_cut(self):
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "scenes_are_same_native_pixels"):
            verifier.inspect_selected_pixels(pixels(no_cut=True), 72, 72)

    def test_same_caption_text_twice_is_not_the_original_authored_source(self):
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "static_or_forged"):
            verifier.inspect_selected_pixels(pixels(same_captions=True), 72, 72)

    def test_missing_original_frame_prevents_physical_pass(self):
        selected = pixels()
        selected.pop(179)
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "sampled_frame_indices"):
            verifier.inspect_selected_pixels(selected, 72, 72)

    def test_corrupt_rgb_bytes_cannot_be_interpreted_as_native_pixels(self):
        selected = pixels()
        selected[33] = b"truncated"
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "sampled_rgb_geometry"):
            verifier.inspect_selected_pixels(selected, 72, 72)

    def test_hash_and_duplicate_json_key_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.json"
            data = b'{"id":"once","id":"twice"}\n'
            path.write_bytes(data)
            original = hashlib.sha256(data).hexdigest()
            with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "duplicate_json_key"):
                verifier.load_json(path, original)
            path.write_bytes(data + b" ")
            with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "media_sha256_changed"):
                verifier.load_json(path, original)


if __name__ == "__main__":
    unittest.main()
