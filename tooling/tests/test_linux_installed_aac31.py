from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_installed_aac31 as installed
from linux_hybrid_original_receipt import Master, Source, original_master
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
    def test_navigation_and_source_check_close_before_the_only_create_worker(self):
        binding = {"pid": 9001, "window": "0x42", "start_time": "100"}
        report = {"phase": "render", "binding": binding}
        with patch.object(installed, "observe_stage", side_effect=[{}, {}, {}, report]) as stage, patch.object(
            installed, "await_original", return_value=Mock(),
        ) as original:
            result = installed.observe_window(Path("request.json"), Path("evidence"), "render", binding,
                                             time.monotonic() + 300, Mock(), Mock(), Mock())
        self.assertEqual(result, report)
        self.assertEqual([call.args[2] for call in stage.call_args_list],
                         ["navigate-render", "check-source", "create", "render"])
        self.assertEqual([call.kwargs.get("limit") for call in stage.call_args_list],
                         [35, 30, 90, None])
        original.assert_called_once()

        with patch.object(installed, "observe_stage", side_effect=[{}, TimeoutError("source observer hung")]) as stage:
            with self.assertRaises(TimeoutError):
                installed.observe_window(Path("request.json"), Path("evidence"), "render", binding,
                                         time.monotonic() + 300, Mock(), Mock(), Mock())
        self.assertEqual([call.args[2] for call in stage.call_args_list], ["navigate-render", "check-source"])

    def test_button_scan_does_not_read_unrelated_document_names(self):
        bridge = installed.ReviewBridge.__new__(installed.ReviewBridge)
        bridge.deadline = None
        bridge.roles = {1: "text", 2: "button"}
        bridge.states = {"visible": "visible", "enabled": "enabled"}
        bridge._button_names_only = True
        state = SimpleNamespace(contains=lambda value: value in {"visible", "enabled"})
        unrelated = SimpleNamespace(get_role=lambda: 1, get_state_set=lambda: state,
                                    get_name=Mock(side_effect=AssertionError("unrelated IPC")))
        control = SimpleNamespace(get_role=lambda: 2, get_state_set=lambda: state,
                                  get_name=lambda: "Check hybrid sources")
        self.assertEqual(bridge.view(unrelated).name, "")
        self.assertEqual(bridge.view(control).name, "Check hybrid sources")
        unrelated.get_name.assert_not_called()

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

    def test_viewport_is_measured_after_scrolling_and_before_one_play(self):
        binding = {"pid": 9001, "window": "0x42", "start_time": "100"}
        player = installed.ui.View("player", "video", installed.PLAYER, frozenset({"visible"}), frozenset())
        buttons = frozenset({"visible", "showing", "enabled", "sensitive"})
        play = installed.ui.View("play", "button", "Play", buttons, frozenset())
        attempts = []

        def scroll(node, _):
            self.assertEqual(node, "player")
            attempts.append("scroll")
            return True

        def view(node):
            if node == "play":
                return play
            return installed.ui.View("player", "video", installed.PLAYER,
                                     frozenset({"visible", "showing"}), frozenset())

        def extents(node, _):
            self.assertEqual(node, "player")
            self.assertEqual(attempts[0], "scroll")
            return SimpleNamespace(x=64, y=120, width=160, height=90)

        def start(node, index):
            self.assertEqual((node, index), ("play", 0))
            attempts.append("play")
            return True

        def capture(_, x, y, width, height):
            self.assertEqual((x, y, width, height), (64, 120, 160, 90))
            self.assertEqual(attempts, ["scroll", "play"])
            observed = next(frames)
            return SimpleNamespace(get_width=lambda:160, get_height=lambda:90,
                                   get_rowstride=lambda:480, get_n_channels=lambda:3,
                                   get_pixels=lambda:observed.pixels)

        frames = iter(map(frame, ("motion-canvas", "manim")))
        api = SimpleNamespace(Component=SimpleNamespace(scroll_to=scroll, get_extents=extents),
                              ScrollType=SimpleNamespace(ANYWHERE=0), CoordType=SimpleNamespace(SCREEN=0))
        bridge = SimpleNamespace(api=api, deadline=time.monotonic() + 5, pump=lambda:None,
                                 owner_pid=lambda _:9001, call=lambda f,*a:f(*a),
                                 view=view, children=lambda n:["play"] if n=="player" else [],
                                 actions=lambda _:["click"], invoke=start)
        panel = SimpleNamespace(get_width=lambda:1440, get_height=lambda:940)
        gdk = SimpleNamespace(get_default_root_window=lambda:panel, pixbuf_get_from_window=capture)
        with tempfile.TemporaryDirectory() as scratch, patch.object(
            installed.ui, "x11_identity", return_value=binding,
        ):
            evidence = Path(scratch)
            result = installed.player_proof(bridge, binding, player, Mock(), gdk, evidence)
            self.assertEqual(attempts, ["scroll", "play"])
            self.assertEqual([sample["scene"] for sample in result], ["motion-canvas", "manim"])
            for sample in result:
                self.assertEqual(sha256((evidence / sample["original"]).read_bytes()).hexdigest(),
                                 sample["sha256"])

    def test_selects_digest_in_the_original_receipt_row_even_if_completed_card_repeats_it(self):
        digest = "a" * 64
        receipt = "fd12950f-8495-47fa-b6d7-d5698b852366"
        review = "Review saved original " + receipt
        master = Master(receipt, receipt, digest, 256, Path("master.mp4"))
        ready = frozenset({"visible", "showing", "enabled", "sensitive"})

        def item(node, role, name):
            return installed.ui.View(node, role, name, ready, frozenset())

        views = {
            "deliver": item("deliver", "button", "Deliver"),
            "check": item("check", "button", "Check hybrid sources"),
            "create": item("create", "button", "Create hybrid master"),
            "find": item("find", "button", "Find saved MP4s"),
            "complete": item("complete", "status", "Completed original hybrid master"),
            "completed-digest": item("completed-digest", "text", digest),
            "saved": item("saved", "section", "Saved original MP4s"),
            "row": item("row", "list item", ""),
            "saved-digest": item("saved-digest", "text", digest),
            "review": item("review", "button", review),
            "player": item("player", "video", installed.PLAYER),
            "metadata": item("metadata", "text", installed.METADATA),
        }
        children = {"complete": ["completed-digest"], "saved": ["find", "row"],
                    "row": ["saved-digest", "review"]}
        initial = ["deliver", "check", "create", "find", "complete", "completed-digest", "saved"]
        action = []

        def invoke(_, __, target, ___, stage):
            action.append((target.node, stage))

        def tree(_, *, buttons_only=False):
            known = initial + (["row", "saved-digest", "review"] if ("find", "find-saved") in action else [])
            return [views[node] for node in known + ["player", "metadata"]]

        bridge = SimpleNamespace(tree=tree, view=views.__getitem__,
                                 children=lambda node: children.get(node, ()),
                                 deadline=time.monotonic() + 5)
        with patch.object(installed, "invoke", side_effect=invoke), patch.object(
            installed, "original_master", return_value=master,
        ), patch.object(
            installed, "same_window",
        ), patch.object(installed, "player_proof", return_value=["played"]):
            result = installed.operate_window(
                {"evidence": "."}, "render", {}, bridge, Mock(), Mock(), Mock(), Mock(), Mock(),
            )
        self.assertEqual(result["master"]["sha256"], digest)
        self.assertEqual([name for node, name in action if node == "review"], ["select-original"])

        views["saved-digest"] = item("saved-digest", "text", "b" * 64)
        with self.assertRaisesRegex(installed.ui.NotReady, "MISSING_EXACT_AAC31_CONTROL"):
            installed.saved_review_control(bridge, {}, master)

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
