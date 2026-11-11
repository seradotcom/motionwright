"""Source-specific negative controls; never infer physical render from unit tests."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
            "frame_rate": {"num": "30", "den": "1"},
            "voice_track_id": voice,
        }],
        "scenes": [
            {
                "id": scene1, "nodes": [{"id": i} for i in nodes[:3]],
                "start": {"num": "0", "den": "1"},
                "duration": {"num": "3", "den": "1"},
            },
            {
                "id": scene2, "nodes": [{"id": nodes[3]}],
                "start": {"num": "3", "den": "1"},
                "duration": {"num": "3", "den": "1"},
            },
        ],
        "audio": {
            "voice_tracks": [{
                "id": voice, "source_sha256": sha, "sample_rate_hz": 48000,
                "channels": 2,
                "measured_duration": {"num": "6", "den": "1"},
            }],
            "transcript": [
                {
                    "voice_track_id": voice, "start": {"num": "1", "den": "5"},
                    "end": {"num": "9", "den": "10"},
                    "alignment": {"kind": "manual"}, "text": "Opening title",
                },
                {
                    "voice_track_id": voice, "start": {"num": "11", "den": "10"},
                    "end": {"num": "12", "den": "5"},
                    "alignment": {"kind": "manual"}, "text": "Following caption",
                },
            ],
        },
        "production_design": {
            "delivery_recipes": {
                profile: {
                    "scene_intents": [
                        {"scene_id": scene1, "role": "evidence", "archetype": "evidence_frame"},
                        {"scene_id": scene2, "role": "evidence", "archetype": "statement"},
                    ],
                },
            },
        },
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
    # The canonical seed updates the first default profile and retains the others.
    for number, (width, height) in enumerate(((1080, 1920), (1080, 1080)), start=21):
        other = copy.deepcopy(source["deliverables"][0])
        other.update(id=f"00000000-0000-4000-8000-{number:012x}",
                     width=width, height=height, captions=False,
                     burn_in_captions=False, voice_track_id=None)
        source["deliverables"].append(other)
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
        self.assertEqual(len(src["deliverables"]), 3)
        self.assertEqual(out["deliverable_id"], src["deliverables"][0]["id"])
        self.assertEqual(len(out["scene_ids"]), 2)
        self.assertEqual(len(out["node_ids"]), 4)
        self.assertEqual(out["revision"], 11)

    def test_deliverable_inventory_must_be_nonempty_objects(self):
        for invalid in (None, {}, [], [None], ["profile"]):
            with self.subTest(invalid=invalid):
                src, film, master = original_source_and_receipts()
                src["deliverables"] = invalid
                with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "deliverable_inventory"):
                    verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_deliverable_ids_must_be_valid_uuid_strings(self):
        for invalid in (None, True, {}, "invalid", "00000000-0000-4000-8000-00000000000A"):
            with self.subTest(invalid=invalid):
                src, film, master = original_source_and_receipts()
                src["deliverables"][1]["id"] = invalid
                with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "deliverable_ids"):
                    verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_duplicate_selected_deliverable_is_rejected(self):
        src, film, master = original_source_and_receipts()
        src["deliverables"][1]["id"] = src["deliverables"][0]["id"]
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "deliverable_ids"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_duplicate_unselected_deliverable_is_rejected(self):
        src, film, master = original_source_and_receipts()
        src["deliverables"][2]["id"] = src["deliverables"][1]["id"]
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "deliverable_ids"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_receipts_cannot_select_another_retained_deliverable(self):
        for changed in ("motion", "master", "both"):
            with self.subTest(changed=changed):
                src, film, master = original_source_and_receipts()
                other = src["deliverables"][1]["id"]
                if changed in ("motion", "both"):
                    film["deliverable_id"] = other
                if changed in ("master", "both"):
                    master["deliverable_id"] = other
                with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "deliverable_identity"):
                    verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_reader_does_not_search_past_changed_first_profile(self):
        src, film, master = original_source_and_receipts()
        src["deliverables"][0], src["deliverables"][1] = (
            src["deliverables"][1], src["deliverables"][0])
        with self.assertRaises(verifier.OriginalMediaMismatch):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_multiple_deliverables_do_not_relax_original_scene_graph(self):
        for shape in ([], [3], [3, 1, 1], [2, 1], [3, 0], [4, 1]):
            with self.subTest(shape=shape):
                src, film, master = original_source_and_receipts()
                templates = copy.deepcopy(src["scenes"])
                src["scenes"] = []
                for i, count in enumerate(shape):
                    scene = copy.deepcopy(templates[min(i, 1)])
                    scene["nodes"] = [{"id": UUIDS[6]} for _ in range(count)]
                    src["scenes"].append(scene)
                with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "scene_graph_changed"):
                    verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_saved_rational_scene_boundary_not_silently_retimed(self):
        src, film, master = original_source_and_receipts()
        src["scenes"][1]["start"]["num"] = "4"
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "rational_clock_changed"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_manually_aligned_cue_windows_bound_to_original_voice(self):
        src, film, master = original_source_and_receipts()
        src["audio"]["transcript"][1]["end"]["num"] = "74"
        src["audio"]["transcript"][1]["end"]["den"] = "30"
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "caption_cues_not_original"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_rewritten_narrative_recipe_not_authorial_original(self):
        src, film, master = original_source_and_receipts()
        pid = src["deliverables"][0]["id"]
        src["production_design"]["delivery_recipes"][pid]["scene_intents"][0]["role"] = "hook"
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "narrative_intents_changed"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

    def test_retimed_voice_duration_not_original_six_seconds(self):
        src, film, master = original_source_and_receipts()
        src["audio"]["voice_tracks"][0]["measured_duration"]["num"] = "5"
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch, "stereo_voice"):
            verifier.source_identity(src, film, master, "ab" * 32, "cd" * 32)

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


class _GrowingHashReader(io.BytesIO):
    """Never EOF, with an independent guard so a broken hash loop cannot hang."""

    def __init__(self):
        super().__init__()
        self.read_requests = []
        self.bytes_returned = 0
        self.iteration_guard_triggered = False

    def read(self, size=-1):
        self.read_requests.append(size)
        if len(self.read_requests) > 16:
            self.iteration_guard_triggered = True
            raise AssertionError("growing-reader guard reached; hashing did not stop")
        if size <= 0:
            raise AssertionError("a positive read size is required")
        chunk = b"x" * min(size, 3)
        self.bytes_returned += len(chunk)
        return chunk


class Film180HashReadBoundsTests(unittest.TestCase):
    @staticmethod
    def _path_with_stream(stream, initial_size):
        path = mock.Mock(spec=Path)
        path.is_absolute.return_value = True
        path.is_file.return_value = True
        path.is_symlink.return_value = False
        path.parents = ()
        path.stat.return_value = mock.Mock(
            st_dev=1, st_ino=2, st_size=initial_size, st_mtime_ns=3,
        )
        path.open.return_value = stream
        return path

    def test_growing_file_rejected_after_initial_size_plus_one_bytes(self):
        initial_size = 7
        stream = _GrowingHashReader()
        path = self._path_with_stream(stream, initial_size)
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^original_media_changed_during_read$"):
            verifier.digest(path, 100, "0" * 64)
        self.assertEqual(stream.bytes_returned, initial_size + 1)
        self.assertFalse(stream.iteration_guard_triggered)
        self.assertTrue(stream.closed)
        self.assertEqual(path.stat.call_count, 1)

    def test_eof_must_match_initial_size_even_when_shorter_bytes_match_digest(self):
        data = b"short"
        stream = io.BytesIO(data)
        path = self._path_with_stream(stream, len(data) + 1)
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^original_media_changed_during_read$"):
            verifier.digest(path, 100, hashlib.sha256(data).hexdigest())
        self.assertTrue(stream.closed)
        self.assertEqual(path.stat.call_count, 1)

    def test_nonempty_short_reads_can_reach_the_exact_initial_size(self):
        class PartialReader(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 3))

        data = b"stable fragmented input"
        stream = PartialReader(data)
        path = self._path_with_stream(stream, len(data))
        expected = hashlib.sha256(data).hexdigest()
        self.assertEqual(verifier.digest(path, 100, expected), expected)
        self.assertTrue(stream.closed)

    def test_stable_physical_files_retain_their_digest(self):
        with tempfile.TemporaryDirectory(prefix="film180-reader-hash-") as directory:
            path = Path(directory) / "media.bin"
            for size in (1, 1024 * 1024, 1024 * 1024 + 7):
                with self.subTest(size=size):
                    data = b"x" * size
                    path.write_bytes(data)
                    expected = hashlib.sha256(data).hexdigest()
                    self.assertEqual(verifier.digest(path, size, expected), expected)

    def test_stable_physical_file_with_wrong_digest_still_fails(self):
        with tempfile.TemporaryDirectory(prefix="film180-reader-hash-") as directory:
            path = Path(directory) / "media.bin"
            path.write_bytes(b"stable source")
            with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                        "^original_media_sha256_changed$"):
                verifier.digest(path, 100, "0" * 64)

    def test_initial_size_cap_is_checked_before_open(self):
        maximum = 100
        for size in (0, maximum + 1):
            with self.subTest(size=size):
                path = self._path_with_stream(io.BytesIO(b"x"), size)
                with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                            "^original_media_byte_budget$"):
                    verifier.digest(path, maximum, "0" * 64)
                path.open.assert_not_called()
                path.open.return_value.close()

    def test_all_final_identity_fields_are_checked(self):
        data = b"stable source"
        fields = {"st_dev": 1, "st_ino": 2, "st_size": len(data), "st_mtime_ns": 3}
        for changed_field in fields:
            with self.subTest(field=changed_field):
                path = self._path_with_stream(io.BytesIO(data), len(data))
                after = dict(fields)
                after[changed_field] += 1
                path.stat.side_effect = [path.stat.return_value, mock.Mock(**after)]
                with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                            "^original_media_changed_during_read$"):
                    verifier.digest(path, 100, hashlib.sha256(data).hexdigest())


class OriginalFFprobeProcessBoundTests(unittest.TestCase):
    """Inert Python child only: actual pipe bytes, no media or Native Host."""

    def test_small_read_only_child_retains_exact_stdout(self):
        command = [sys.executable, "-I", "-c",
                   "import sys;sys.stdout.buffer.write(b'original\\n');"
                   "sys.stderr.buffer.write(b'bounded')"]
        self.assertEqual(
            verifier._bounded_ffprobe(command, timeout=8,
                over_limit="original_ffprobe_receipt_unbounded"),
            b"original\n",
        )

    def test_oversized_child_stdout_is_rejected_while_streaming(self):
        command = [sys.executable, "-I", "-c",
                   "import sys;sys.stdout.buffer.write(b'x'*(4194304+1))"]
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^original_ffprobe_receipt_unbounded$"):
            verifier._bounded_ffprobe(command, timeout=8,
                                     over_limit="original_ffprobe_receipt_unbounded")

    def test_oversized_child_stderr_is_also_bounded_in_flight(self):
        command = [sys.executable, "-I", "-c",
                   "import sys;sys.stderr.buffer.write(b'y'*(65536+1))"]
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^original_ffprobe_stderr_unbounded$"):
            verifier._bounded_ffprobe(command, timeout=8,
                                     over_limit="original_ffprobe_receipt_unbounded")


    def test_exited_parent_with_inherited_child_pipes_remains_bounded_unknown(self):
        # The direct child exits cleanly, but a self-terminating descendant
        # inherits both stream handles and keeps them open. The original
        # transport is incomplete, not an empty stream or a completed ffprobe.
        script = (
            "import subprocess,sys;"
            "subprocess.Popen([sys.executable,'-I','-c',"
            "'import time; time.sleep(2)'],stdin=subprocess.DEVNULL,"
            "stdout=sys.stdout,stderr=sys.stderr,shell=False);"
            "sys.stdout.buffer.write(b'original\\n');"
            "sys.stdout.buffer.flush()"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^original_ffprobe_pipe_uncertain$"):
            verifier._bounded_ffprobe(
                [sys.executable, "-I", "-c", script], timeout=0.25,
                over_limit="original_ffprobe_receipt_unbounded",
            )
        self.assertLess(time.monotonic() - started, 1.7)


    def test_psnr_auxiliary_pipes_use_four_and_eight_kib_inflight(self):
        # Pure Python source-only child: no PNG, FFmpeg or Native render.
        for stream, size in (("stdout", 4097), ("stderr", 8193)):
            with self.subTest(stream=stream):
                command = [sys.executable, "-I", "-c",
                           "import sys;sys." + stream
                           + ".buffer.write(b'x'*" + str(size) + ")"]
                with self.assertRaisesRegex(
                        verifier.OriginalMediaMismatch,
                        "^RGB_PSN_FFmpeg_source_comparison_not_bounded$"):
                    verifier._bounded_readonly_output(
                        command, timeout=8, stdout_limit=4096,
                        stderr_limit=8192,
                        over_limit="RGB_PSN_FFmpeg_source_comparison_not_bounded",
                        stderr_over_limit="RGB_PSN_FFmpeg_source_comparison_not_bounded",
                    )

    def test_all180_hash_manifest_pipe_rejects_128k_plus_one_in_flight(self):
        # A source-only Python process exercises the real pipe ceiling. No
        # FFmpeg launch, media decoding, Native effect, or copied frame hashes.
        command = [sys.executable, "-I", "-c",
                   "import sys;sys.stdout.buffer.write(b'h'*(131072+1))"]
        with self.assertRaisesRegex(
                verifier.OriginalMediaMismatch,
                "^all180_decoded_RGB_hash_receipt_budget$"):
            verifier._bounded_readonly_output(
                command, timeout=8, stdout_limit=128 * 1024,
                over_limit="all180_decoded_RGB_hash_receipt_budget",
            )

    def test_selected_rgb_pipe_has_its_exact_precharged_output_ceiling(self):
        # Inert decoder, not an MP4 or Native Host. A small output cap lets
        # this test check the real stream boundary without allocating 75 MiB.
        argv = [sys.executable, "-I", "-c",
                "import sys;sys.stdout.buffer.write(b'a'*33)"]
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^selected_decoded_native_frame_bytes_mismatch$"):
            verifier._bounded_readonly_output(
                argv, timeout=8, stdout_limit=32,
                over_limit="selected_decoded_native_frame_bytes_mismatch",
            )
        self.assertEqual(
            verifier._bounded_readonly_output(
                [sys.executable, "-I", "-c",
                 "import sys;sys.stdout.buffer.write(b'a'*32)"],
                timeout=8, stdout_limit=32,
                over_limit="selected_decoded_native_frame_bytes_mismatch",
            ),
            b"a" * 32,
        )

    def test_selected_video_exact_rgb_geometry_uses_the_bounded_supervisor(self):
        # A tiny synthetic output tests split/join logic without a renderer.
        raw = b"ABCDEFghijkl"
        with mock.patch.object(verifier, "FRAME_BYTES", 6), \
                mock.patch.object(verifier, "SELECTED", (0, 5)), \
                mock.patch.object(verifier, "_bounded_readonly_output",
                                  return_value=raw) as bounded:
            actual = verifier.decode_selected_video(
                Path("not-dispatched.mp4"), "ffmpeg")
            self.assertEqual(actual, {0: b"ABCDEF", 5: b"ghijkl"})
            self.assertEqual(bounded.call_count, 1)
            self.assertEqual(bounded.call_args.kwargs["stdout_limit"], 12)
            self.assertEqual(bounded.call_args.kwargs["timeout"], 150)
            self.assertEqual(
                bounded.call_args.kwargs["over_limit"],
                "selected_decoded_native_frame_bytes_mismatch",
            )
            bounded.return_value = raw[:-1]
            with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                        "^selected_decoded_native_frame_bytes_mismatch$"):
                verifier.decode_selected_video(
                    Path("not-dispatched.mp4"), "ffmpeg")


class OriginalPCMBoundedCaptureTests(unittest.TestCase):
    """Bounded original PCM decode custody, with no external FFmpeg process."""

    def test_original_pcm_observed_once_before_returning_the_same_bytes(self):
        pcm = b"\x01\x00\x02\x00" * verifier.PCM_FRAMES
        seen = []
        def source_decoder(_argv, *, observed, stdout_limit, **_kwargs):
            self.assertEqual(stdout_limit, verifier.AUDIO_BYTES_MAX)
            observed.update(stdout=pcm, stderr=b"original stderr",
                            exit_code=0, deadline_exceeded=False,
                            automatic_retry=False)
            return pcm
        with mock.patch.object(verifier, "_bounded_readonly_output",
                               side_effect=source_decoder) as decode:
            result = verifier.decode_original_audio(
                Path("not-dispatched.mp4"), "ffmpeg", capture=seen.append)
        decode.assert_called_once()
        self.assertIs(result, pcm)
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]["stdout"], pcm)
        self.assertEqual(seen[0]["stderr"], b"original stderr")
        self.assertEqual(seen[0]["exit_code"], 0)
        self.assertFalse(seen[0]["deadline_exceeded"])
        self.assertFalse(seen[0]["automatic_retry"])

    def test_partial_pcm_retained_once_without_promoting_a_failed_decoder(self):
        error = verifier.OriginalMediaMismatch(
            "full_original_aac_pcm_sample_budget_changed")
        seen = []
        def source_decoder(_argv, *, observed, **_kwargs):
            observed.update(stdout=b"partial\x00", stderr=b"original",
                            exit_code=None, deadline_exceeded=False,
                            automatic_retry=False)
            raise error
        def failing_capture(value):
            seen.append(value)
            raise OSError("private original disk failed")
        with mock.patch.object(verifier, "_bounded_readonly_output",
                               side_effect=source_decoder) as decode:
            with self.assertRaises(verifier.OriginalMediaMismatch) as raised:
                verifier.decode_original_audio(
                    Path("not-dispatched.mp4"), "ffmpeg",
                    capture=failing_capture)
        decode.assert_called_once()
        self.assertIs(raised.exception, error)
        self.assertTrue(error.private_capture_failed)
        self.assertEqual(len(seen), 1)
        self.assertIsNone(seen[0]["exit_code"])
        self.assertEqual(seen[0]["stdout"], b"partial\x00")

    def test_short_original_pcm_is_captured_then_rejected_not_adjusted(self):
        short_pcm = b"\x00" * (verifier.PCM_FRAMES * 4 - 4)
        seen = []
        def source_decoder(_argv, *, observed, **_kwargs):
            observed.update(stdout=short_pcm, stderr=b"", exit_code=0,
                            deadline_exceeded=False, automatic_retry=False)
            return short_pcm
        with mock.patch.object(verifier, "_bounded_readonly_output",
                               side_effect=source_decoder) as decode:
            with self.assertRaisesRegex(
                    verifier.OriginalMediaMismatch, "sample_budget_changed"):
                verifier.decode_original_audio(
                    Path("not-dispatched.mp4"), "ffmpeg", capture=seen.append)
        decode.assert_called_once()
        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0]["stdout"], short_pcm)

    def test_over_budget_child_retains_bounded_original_not_an_exit_zero(self):
        observed = {}
        argv = [sys.executable, "-I", "-c",
                "import sys;sys.stdout.buffer.write(b'x'*33)"]
        with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                    "^full_original_aac_pcm_sample_budget_changed$"):
            verifier._bounded_readonly_output(
                argv, timeout=8, stdout_limit=32,
                over_limit="full_original_aac_pcm_sample_budget_changed",
                observed=observed)
        self.assertEqual(observed["stdout"], b"x" * 33)
        self.assertEqual(observed["exit_code"], None)
        self.assertFalse(observed["automatic_retry"])


class OriginalFFprobeStreamBoundTests(unittest.TestCase):
    """Both original inventories have an equal finite JSON receipt ceiling."""

    @staticmethod
    def data(*, oversized=False):
        frames = [{"media_type": "video", "best_effort_timestamp_time": str(i / 30)}
                  for i in range(verifier.FRAME_COUNT)]
        video = {"codec_type": "video", "codec_name": "h264",
                 "width": verifier.WIDTH, "height": verifier.HEIGHT,
                 "avg_frame_rate": "30/1"}
        audio = {"codec_type": "audio", "codec_name": "aac",
                 "sample_rate": str(verifier.PCM_RATE), "channels": 2}
        first = json.dumps({"frames": frames}).encode()
        second = json.dumps({"streams": [video, audio],
                             "unused": "x" * (4 * 1024 * 1024) if oversized else ""}).encode()
        return first, second

    def test_all_streams_original_rejects_oversize_before_json_parsing(self):
        first, second = self.data(oversized=True)
        self.assertLess(len(first), 4 * 1024 * 1024)
        self.assertGreater(len(second), 4 * 1024 * 1024)
        with mock.patch.object(verifier, "_bounded_ffprobe",
                               side_effect=[first, second]) as run:
            with self.assertRaisesRegex(verifier.OriginalMediaMismatch,
                                        "^original_ffprobe_stream_inventory_unbounded$"):
                verifier.ffprobe_media(Path("not-dispatched.mp4"), "ffprobe")
            self.assertEqual(run.call_count, 2)

    def test_two_small_original_stream_inventories_are_supported(self):
        first, second = self.data()
        with mock.patch.object(verifier, "_bounded_ffprobe",
                               side_effect=[first, second]) as run:
            observed = verifier.ffprobe_media(Path("not-dispatched.mp4"), "ffprobe")
            self.assertEqual(observed["video_frames_decoded"], verifier.FRAME_COUNT)
            self.assertEqual(observed["audio_codec"], "aac")
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
