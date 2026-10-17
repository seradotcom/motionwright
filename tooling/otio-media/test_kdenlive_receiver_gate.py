"""Reader tests with declared synthetic records; no Kdenlive run is simulated."""
from __future__ import annotations

import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import bridge
import kdenlive_receiver_gate as gate


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def source_hashes() -> list[str]:
    # These are distinct reader-fixture signatures, not hashes of native images.
    return [digest("inspector fixture frame " + str(at)) for at in range(90)]


def framehash(count: int = 75) -> bytes:
    return ("#format: frame checksums\n#version: 2\n#hash: SHA256\n#tb 0: 1/30\n"
            "#media_type 0: video\n#codec_id 0: rawvideo\n#dimensions 0: 320x180\n"
            + "\n".join(f"0, {i}, {i}, 1, 172800, {value}" for i, value in
                        enumerate(source_hashes()[:count])) + "\n").encode()


def profile() -> dict:
    return {"codec": "ffv1", "demuxer": "matroska", "pix_fmt": "yuv420p",
            "color_range": "tv", "color_space": "bt709", "color_transfer": "bt709",
            "color_primaries": "bt709"}


def probe_fixture(count: int = 75, quantized: bool = False) -> tuple[dict, dict, dict]:
    scale = 1000 if quantized else 15360
    points = [round(Fraction(i * scale, 30)) for i in range(count)]
    duration = round(Fraction(scale, 30))
    stream = {"index": 0, "codec_type": "video", "codec_name": "ffv1",
              "width": 320, "height": 180, "sample_aspect_ratio": "1:1",
              "field_order": "progressive", "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
              "time_base": "1/" + str(scale), **{k: v for k, v in profile().items()
                                                if k not in {"codec", "demuxer"}}}
    frames = [{"media_type": "video", "stream_index": 0, "width": 320, "height": 180,
               "sample_aspect_ratio": "1:1", "interlaced_frame": 0, "pix_fmt": "yuv420p",
               "pts": pts, "best_effort_timestamp": pts} for pts in points]
    packets = [{"stream_index": 0, "pts": pts, "dts": pts - 2 * duration,
                "duration": duration} for pts in points]
    return {"streams": [stream]}, {"frames": frames}, {"packets": packets}


class WholeFrameIdentityTests(unittest.TestCase):
    def test_exact_ninety_and_seventy_five_frames_use_every_source_index(self):
        original = source_hashes()
        for count in (90, 75):
            with self.subTest(count=count):
                report = gate.compare_frames(original, original[:count], count)
                self.assertEqual(report["source_interval"], [0, count])
                self.assertTrue(report["every_frame_digest_equal"])
                self.assertFalse(report["first_last_only"])

    def test_wrong_count_or_partial_decoder_output_is_rejected(self):
        for count in (74, 76):
            with self.subTest(count=count), self.assertRaisesRegex(gate.GateRefused, "frame_sequence_length_changed"):
                gate.compare_frames(source_hashes(), source_hashes()[:count], 75)
        with self.assertRaisesRegex(gate.GateRefused, "incomplete_decoded_framehash"):
            gate.parse_framehash(framehash(74), 75)

    def test_duplicate_reorder_foreign_middle_and_last_frame_cannot_hide_behind_endpoints(self):
        original = source_hashes()
        cases = []
        duplicated = original[:75]; duplicated[37] = duplicated[36]; cases.append(duplicated)
        reordered = original[:75]; reordered[37], reordered[38] = reordered[38], reordered[37]; cases.append(reordered)
        foreign = original[:75]; foreign[37] = digest("foreign pixel contents"); cases.append(foreign)
        tail = original[:75]; tail[74] = digest("foreign last pixel contents"); cases.append(tail)
        for result in cases:
            with self.subTest(changed=[i for i, h in enumerate(result) if h != original[i]]), \
                    self.assertRaisesRegex(gate.GateRefused, "decoded_source_frame_sequence_changed"):
                gate.compare_frames(original, result, 75)

    def test_periodic_original_is_ineligible_even_when_all_output_hashes_match(self):
        original = source_hashes(); original[46] = original[6]
        with self.assertRaisesRegex(gate.GateRefused, "source_timing_identity_ambiguous"):
            gate.compare_frames(original, original[:75], 75)

    def test_framehash_parser_requires_complete_rgb24_rows_and_declared_hash(self):
        self.assertEqual(gate.parse_framehash(framehash(), 75), source_hashes()[:75])
        for bad, code in ((framehash().replace(b"SHA256", b"MD5"), "wrong_framehash_algorithm"),
                          (framehash().replace(b"172800", b"230400", 1), "framehash_representation_changed"),
                          (framehash().replace(b"0, 37, 37, 1", b"0, 37, 36, 1"), "framehash_output_clock_changed"),
                          (framehash() + b"extra corrupt row\n", "invalid_framehash_row")):
            with self.subTest(code=code), self.assertRaisesRegex(gate.GateRefused, code):
                gate.parse_framehash(bad, 75)


class PhysicalClockProfileTests(unittest.TestCase):
    def test_exact_decoded_and_packet_clocks_cover_every_frame_and_final_interval(self):
        for count in (90, 75):
            report = gate.validate_probe(*probe_fixture(count), count, profile(),
                                         {"mode": "exact", "container_tick": None})
            self.assertEqual(len(report["input_pts"]), count)
            self.assertTrue(report["all_frames_and_packets_checked"])
            self.assertEqual(report["packet_pts_duration"][-1], ((count - 1) * 512, 512))

    def test_native_matroska_tick_requires_the_predeclared_profile(self):
        data = probe_fixture(75, quantized=True)
        with self.assertRaisesRegex(gate.GateRefused, "timestamp_not_exact"):
            gate.validate_probe(*data, 75, profile(), {"mode": "exact", "container_tick": None})
        self.assertEqual(gate.validate_probe(*data, 75, profile(),
                         {"mode": "nearest_tick", "container_tick": "1/1000"})["frames"], 75)

    def test_one_frame_offset_and_small_accumulated_drift_are_not_rounded_away(self):
        for change in (512, 1):
            data = probe_fixture()
            for row in data[1]["frames"]:
                row["pts"] += change; row["best_effort_timestamp"] += change
            for row in data[2]["packets"]:
                row["pts"] += change
            with self.subTest(offset=change), self.assertRaisesRegex(gate.GateRefused, "timestamp_not_exact"):
                gate.validate_probe(*data, 75, profile(), {"mode": "exact", "container_tick": None})
        data = probe_fixture(75, quantized=True)
        for row in data[1]["frames"][50:]:
            row["pts"] += 1; row["best_effort_timestamp"] += 1
        with self.assertRaisesRegex(gate.GateRefused, "timestamp_outside_declared_quantization"):
            gate.validate_probe(*data, 75, profile(), {"mode": "nearest_tick", "container_tick": "1/1000"})

    def test_missing_packets_tail_and_decoder_repair_cannot_hide_behind_fps(self):
        for name in ("missing_packet", "short_last_packet", "repaired_frame"):
            data = probe_fixture()
            if name == "missing_packet":
                data[2]["packets"].pop(40)
            elif name == "short_last_packet":
                data[2]["packets"][-1]["duration"] = 1
            else:
                data[1]["frames"][1]["best_effort_timestamp"] += 1
            with self.subTest(name=name), self.assertRaises(gate.GateRefused):
                gate.validate_probe(*data, 75, profile(), {"mode": "exact", "container_tick": None})

    def test_audio_geometry_interlace_and_color_outside_locked_profile_are_refused(self):
        changes = ("audio", "width", "interlace", "color", "decoded_size", "fps")
        for change in changes:
            data = probe_fixture()
            if change == "audio": data[0]["streams"].append({"index": 1, "codec_type": "audio"})
            if change == "width": data[0]["streams"][0]["width"] = 640
            if change == "interlace": data[1]["frames"][20]["interlaced_frame"] = 1
            if change == "color": data[0]["streams"][0]["color_range"] = "pc"
            if change == "decoded_size": data[1]["frames"][20]["height"] = 181
            if change == "fps": data[0]["streams"][0]["avg_frame_rate"] = "30000/1001"
            with self.subTest(change=change), self.assertRaises(gate.GateRefused):
                gate.validate_probe(*data, 75, profile(), {"mode": "exact", "container_tick": None})

    def test_unbounded_or_coarse_quantization_is_refused_before_a_probe(self):
        for clock in ({"mode": "nearest_tick", "container_tick": "1/30"},
                      {"mode": "nearest_tick", "container_tick": "0/1"},
                      {"mode": "exact", "container_tick": "1/1000"},
                      {"mode": "round_frame_index", "container_tick": "1/1000"}):
            with self.subTest(clock=clock), self.assertRaises(gate.GateRefused):
                gate.validate_clock_profile(clock)


class FileAndLossBoundaryTests(unittest.TestCase):
    def test_bound_file_refuses_changed_bytes_and_every_escape_spelling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "record.json"; file.write_text("inspector record")
            row = {"path": "record.json", **gate.file_record(file)}
            self.assertEqual(gate.bound_file(root, row), file)
            file.write_text("changed inspector record")
            with self.assertRaisesRegex(gate.GateRefused, "evidence_bytes_changed"):
                gate.bound_file(root, row)
            for name in ("../record.json", "./record.json", "/record.json", "C:record.json",
                         "x\\record.json", "a//record.json", "record.json:stream", "x/../record.json"):
                with self.subTest(name=name), self.assertRaisesRegex(gate.GateRefused, "relative_path_required"):
                    gate.relative_name(name)

    def test_duplicate_json_and_oversized_json_are_not_admitted_as_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            path.write_bytes(b'{"schema":"first","schema":"second"}')
            with self.assertRaises(bridge.BridgeRefused): gate.load_json(path)
            path.write_bytes(b" " * (2 * 1024 * 1024 + 1))
            with self.assertRaises(bridge.BridgeRefused): gate.load_json(path)

    def test_missing_reference_must_fail_for_its_own_reason(self):
        result = gate.missing_result({"status": "REJECTED_LOSS",
            "rejection_code": "original_missing_reference_not_receivable", "native_change_proposals": []})
        self.assertEqual(result["status"], "SOURCE_BOUND_MISSING_LOSS_VERIFIED")
        self.assertFalse(result["native_acceptance"])
        for preview in ({"status": "REJECTED_LOSS", "rejection_code": "unsupported_native_schema",
                         "native_change_proposals": []},
                        {"status": "BOUNDED_PROPOSAL_RECEIVER_NOT_VERIFIED", "native_change_proposals": []},
                        {"status": "REJECTED_LOSS", "rejection_code": "original_missing_reference_not_receivable",
                         "native_change_proposals": [{"type": "invent_media"}]}):
            self.assertEqual(gate.missing_result(preview)["status"], "INCONCLUSIVE_MISSING")

    def test_decoder_failure_does_not_publish_partial_frame_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.mkv"; source.write_bytes(b"reader input only")
            lock = {"files": {"ffmpeg": {"path": "not-executed"}, "ffprobe": {"path": "not-executed"}}}
            with mock.patch.object(bridge, "_probe_output", side_effect=bridge.BridgeRefused("decode error")):
                with self.assertRaises(bridge.BridgeRefused):
                    gate.inspect_video(source, 75, profile(), {"mode": "exact", "container_tick": None},
                                       lock, root / "readback")
            self.assertFalse((root / "readback").exists())


class ObservationCustodyTests(unittest.TestCase):
    """An observed action record is never turned into a product authorization."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = {"received_project": "native/received.kdenlive",
                      "edited_project": "native/tail-edited.kdenlive",
                      "received_raw": "raw/received.otio", "edited_raw": "raw/tail-edited.otio",
                      "missing_project": "missing-native/received.kdenlive", "missing_raw": "missing-raw/received.otio",
                      "received_render": "renders/received.mkv", "edited_render": "renders/tail-edited.mkv"}
        # Files are explicit inspector fixtures, not media/native project proofs.
        for name in list(self.paths.values()) + ["complete/timeline.otio", "missing/timeline.otio", "observer.txt"]:
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("INSPECTOR FIXTURE ONLY: " + name)
        self.plan = {"runtime_lock_sha256": digest("runtime lock fixture"), "outputs": self.paths}
        (self.root / "receiver-plan.json").write_bytes(gate.encoded(self.plan))
        self.lock = {"observer": {"controller": "inspector fixture", "session_identity": "fixture-session"},
                     "files": {"preset_parameters": {"sha256": digest("preset fixture")}}}
        stages = ["import", "save_received", "close_received", "reopen_received", "export_received",
                  "render_received", "native_tail_edit", "save_edited", "close_edited", "reopen_edited",
                  "export_edited", "render_edited", "import_missing", "save_missing", "export_missing"]
        self.actions = []
        for at, stage in enumerate(stages):
            process = 1 if at <= 2 else 2 if at <= 8 else 3 if at <= 11 else 4
            if stage.startswith("render_"): process = 100 + at
            self.actions.append({"stage": stage, "case": "missing" if at >= 12 else "complete",
                "process_identity": {"pid": process, "start_token": "process " + str(process)},
                "monotonic_ns": at + 1, "outcome": "KNOWN_COMPLETE", "inputs": [], "outputs": [],
                "evidence": [self.row("observer.txt")], "details": {}})
        by = {a["stage"]: a for a in self.actions}
        by["import"]["inputs"] = [self.row("complete/timeline.otio")]
        by["import_missing"]["inputs"] = [self.row("missing/timeline.otio")]
        for adjective in ("received", "edited"):
            project, raw, render = (self.paths[adjective + "_" + suffix] for suffix in ("project", "raw", "render"))
            by["save_" + adjective]["outputs"] = [self.row(project)]
            by["reopen_" + adjective]["inputs"] = [self.row(project)]
            by["export_" + adjective]["inputs"] = [self.row(project)]
            by["export_" + adjective]["outputs"] = [self.row(raw)]
            by["render_" + adjective]["inputs"] = [self.row(project)]
            by["render_" + adjective]["outputs"] = [self.row(render)]
            by["render_" + adjective]["details"] = {"preset_parameters_sha256": digest("preset fixture"),
                "render_async": False, "exit_code": 0, "native_jobs_created": 1}
            by["close_" + adjective]["details"] = {"process_exited": True, "exit_code": 0}
        by["save_missing"]["outputs"] = [self.row(self.paths["missing_project"])]
        by["export_missing"]["inputs"] = [self.row(self.paths["missing_project"])]
        by["export_missing"]["outputs"] = [self.row(self.paths["missing_raw"])]
        by["native_tail_edit"]["details"] = {"scene_ordinal": 2, "selected_clips": 1, "m_pos_frames": 60,
            "m_cropStart_frames": 60, "m_dur_frames": 15, "external_xml_edit": False}

    def row(self, name):
        return {"path": name, **gate.file_record(self.root / name)}

    def observation(self):
        return {"schema": "motionwright.kdenlive-receiver-observations/1",
                "plan_sha256": gate.file_record(self.root / "receiver-plan.json")["sha256"],
                "runtime_lock_sha256": self.plan["runtime_lock_sha256"], "observer": self.lock["observer"],
                "actions": self.actions}

    def check(self):
        return gate.validate_observations(self.root, self.observation(), self.plan, self.lock)

    def test_consistent_retained_chain_remains_unsigned_and_does_not_claim_native_execution(self):
        result = self.check()
        self.assertTrue(result["reported_save_close_reopen_edit_chain_consistent"])
        self.assertFalse(result["native_execution_authenticated_by_this_json"])
        self.assertEqual(result["observation_authentication"], "REQUIRES_A6_OBSERVER_ADMISSION")

    def test_unknown_import_save_edit_or_render_requires_original_reconciliation(self):
        for stage in ("import", "save_received", "native_tail_edit", "render_edited"):
            action = next(a for a in self.actions if a["stage"] == stage)
            action["outcome"] = "UNKNOWN"
            with self.subTest(stage=stage), self.assertRaisesRegex(gate.GateRefused, "original_action_requires_reconciliation"):
                self.check()
            action["outcome"] = "KNOWN_COMPLETE"

    def test_no_session_records_cannot_be_promoted_by_valid_raw_and_render_names(self):
        observation = self.observation(); observation["actions"] = []
        with self.assertRaisesRegex(gate.GateRefused, "native_action_chain_incomplete"):
            gate.validate_observations(self.root, observation, self.plan, self.lock)

    def test_reopen_in_same_process_and_missing_case_reuse_are_refused(self):
        for target, stages in ((self.actions[0]["process_identity"],
                                ("reopen_received", "export_received", "native_tail_edit", "save_edited", "close_edited")),
                               (self.actions[0]["process_identity"], ("import_missing", "save_missing", "export_missing"))):
            original = copy.deepcopy(self.actions)
            for action in self.actions:
                if action["stage"] in stages: action["process_identity"] = target
            with self.assertRaisesRegex(gate.GateRefused, "reopen_requires_a_new_process"):
                self.check()
            self.actions = original

    def test_render_of_wrong_project_and_stale_export_hash_are_refused(self):
        render = next(a for a in self.actions if a["stage"] == "render_edited")
        render["inputs"] = [self.row(self.paths["received_project"])]
        with self.assertRaisesRegex(gate.GateRefused, "native_action_file_join_missing"):
            self.check()
        render["inputs"] = [self.row(self.paths["edited_project"])]
        (self.root / self.paths["edited_raw"]).write_text("RAW changed after native export")
        with self.assertRaisesRegex(gate.GateRefused, "evidence_bytes_changed"):
            self.check()

    def test_wrong_preset_zero_jobs_and_external_xml_edit_are_refused(self):
        render = next(a for a in self.actions if a["stage"] == "render_received")
        for key, value in (("native_jobs_created", 0), ("render_async", True),
                           ("preset_parameters_sha256", digest("different preset"))):
            original = copy.deepcopy(render["details"]); render["details"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(gate.GateRefused, "native_render_not_bound_to_observed_job"):
                self.check()
            render["details"] = original
        edit = next(a for a in self.actions if a["stage"] == "native_tail_edit")
        edit["details"]["external_xml_edit"] = True
        with self.assertRaisesRegex(gate.GateRefused, "native_tail_edit_profile_changed"):
            self.check()


if __name__ == "__main__":
    unittest.main(verbosity=2)
