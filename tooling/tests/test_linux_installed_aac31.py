from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux_hybrid_original_receipt import Source, original_master
from linux_hybrid_playback_frames import PlaybackFrame, marker, observe_playback


def frame(scene):
    pixels = bytearray(160 * 90 * 3)
    if scene:
        center, color = (40, b"\xff\x00\x00") if scene == "motion-canvas" else (70, b"\x00\xff\x00")
        for y in range(30, 50):
            for x in range(center - 7, center + 7):
                offset = (y * 160 + x) * 3
                pixels[offset:offset + 3] = color
    return PlaybackFrame(160, 90, 480, 3, bytes(pixels))


class InstalledAAC31ProofTests(unittest.TestCase):
    def test_playback_requires_the_two_observed_scene_markers_in_order(self):
        red, green, blank = map(frame, ("motion-canvas", "manim", None))
        self.assertEqual([marker(value) for value in (red, green, blank)],
                         ["motion-canvas", "manim", None])
        captures = iter((blank, red, green))
        start = time.monotonic_ns()
        first, second = observe_playback(
            lambda: next(captures), play_intent_ns=start, deadline_ns=start + 1_000_000_000,
        )
        self.assertEqual((first[0], second[0]), (red, green))
        self.assertLess(first[1], second[1])
        start = time.monotonic_ns()
        with self.assertRaisesRegex(ValueError, "two authored scenes"):
            observe_playback(lambda: red, play_intent_ns=start, deadline_ns=start + 50_000_000)

    def test_recovery_rejects_substituted_bytes_or_a_second_mux_dispatch(self):
        source = Source("735370d7-4c68-4382-b081-ecd3d8687f11",
                        "3c6e0f63-f976-4904-a671-b8348f1a0936", 11,
                        "897e54a3-de24-474b-8e70-365e5e8af251")
        receipt_id = "fd12950f-8495-47fa-b6d7-d5698b852366"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            master = root / "master.mp4"
            content = b"opaque receipt-binding control"
            master.write_bytes(content)
            digest = sha256(content).hexdigest()
            context = {
                "schema": "motionwright.original-hybrid-media-reopen/1",
                "deliverable_id": source.deliverable,
                "args": {"frame_count": 31, "fps_num": 30, "fps_den": 1,
                         "sample_rate": 48_000, "channels": 2, "output_path": master.name},
            }
            result = {
                "original_media_reopen": context,
                "result": {
                    "execution": {"dry_run": False, "provenance": {"provider": "driver:mlt-video"}},
                    "provenance_checked": True,
                    "artifact": {"root": "output", "path": master.name,
                                 "bytes": len(content), "sha256": digest},
                },
            }
            database = root / "studio.sqlite3"
            db = sqlite3.connect(database)
            self.addCleanup(db.close)
            db.execute(
                "CREATE TABLE production_receipts(receipt_id,project_id,generation,revision,"
                "request_id,request_sha256,command,stage,payload_json)"
            )
            def insert(id_, stage, payload):
                db.execute(
                    "INSERT INTO production_receipts VALUES(?,?,?,?,?,?,?,?,?)",
                    (id_, source.project, source.generation, source.revision,
                     "aac31", "0" * 64, "driver.mlt-video.av.mux", stage, json.dumps(payload)),
                )
                db.commit()

            insert("77f261ce-74e1-47e7-8f79-5405462c0100", "dispatching",
                   {"original_media_reopen": context})
            insert(receipt_id, "completed", result)
            original = original_master(database, source, root)
            self.assertEqual((original.id, original.sha256), (receipt_id, digest))
            master.write_bytes(b"x" * len(content))
            with self.assertRaisesRegex(ValueError, "differs from"):
                original_master(database, source, root)
            master.write_bytes(content)
            insert("77b5d9a9-d98c-437a-b895-f8dc33a5b73e", "dispatching",
                   {"original_media_reopen": context})
            with self.assertRaisesRegex(ValueError, "one original completed"):
                original_master(database, source, root)
            db.close()


if __name__ == "__main__":
    unittest.main()
