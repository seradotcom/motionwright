"""Synthetic mathematical regressions, not encoded AAC or native proof."""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_aac_clock_inspection as clock


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("ascii")


def desc(raw):
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def seconds(ticks):
    return format(Decimal(ticks) / Decimal(48000), ".6f")


def fixture(head=240, tail=80, anchor=0, first_discard=False):
    # Deliberately varying lengths and non-1024 trims. The last packet's
    # duration is already the presentation duration, unlike manual nb_samples.
    sizes = [480, 960, 1920, 1920, 560]
    source_count = sum(sizes) - head - tail
    pcm = b"".join(struct.pack(
        "<hh", round(8000 * math.sin(2 * math.pi * 431 * i / 48000)),
        round(9000 * math.sin(2 * math.pi * 713 * i / 48000)))
        for i in range(source_count))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(pcm)
    stream = {
        "index": 1, "codec_name": "aac", "codec_type": "audio",
        "sample_rate": "48000", "channels": 2, "time_base": "1/48000",
        "start_pts": anchor, "start_time": seconds(anchor),
        "duration_ts": source_count, "duration": seconds(source_count),
        "nb_frames": str(len(sizes)), "nb_read_packets": str(len(sizes)),
        "initial_padding": head,
    }
    packets, manual, default = [], [], []
    position, point, remaining_skip = 256, anchor - head, head
    for index, count in enumerate(sizes):
        duration = count - tail if index == len(sizes) - 1 else count
        packet = {
            "codec_type": "audio", "stream_index": 1,
            "pts": point, "dts": point, "duration": duration,
            "pts_time": seconds(point), "dts_time": seconds(point),
            "duration_time": seconds(duration), "pos": str(position),
            "size": "96", "flags": "KD" if first_discard and index == 0 else "K_",
        }
        if index == 0 or index == len(sizes) - 1:
            packet["side_data_list"] = [{
                "side_data_type": "Skip Samples",
                "skip_samples": head if index == 0 else 0,
                "discard_padding": tail if index == len(sizes) - 1 else 0,
                "skip_reason": 0, "discard_reason": 0,
            }]
        frame = {
            "media_type": "audio", "stream_index": 1, "pts": point,
            "nb_samples": count, "channels": 2, "pkt_pos": str(position),
            "pkt_size": "96", "sample_fmt": "fltp",
            "best_effort_timestamp": point, "duration": duration,
        }
        manual.append(frame)
        # This fixture constructs independent observed results of fixed cuts.
        # It is deliberately not a call to the inspector's reconciliation.
        begin_cut = min(count, remaining_skip)
        remaining_skip -= begin_cut
        end_cut = tail if index == len(sizes) - 1 else 0
        if count - begin_cut - end_cut > 0:
            audible = dict(frame)
            audible["pts"] = point + begin_cut
            audible["best_effort_timestamp"] = audible["pts"]
            audible["nb_samples"] = count - begin_cut - end_cut
            default.append(audible)
        packets.append(packet)
        point += count
        position += 128
    artifacts = {
        "original_wav": output.getvalue(),
        "master": b"SYNTHETIC_NON_MEDIA_BYTES_FOR_MATH_TEST_ONLY\n" * 64,
        "packets": packed({"packets": packets, "streams": [stream]}),
        "decoded_pcm": pcm,
        "default_frames": packed({"frames": default, "streams": [stream]}),
        "manual_frames": packed({"frames": manual, "streams": [stream]}),
    }
    binding = {name: desc(raw) for name, raw in artifacts.items()}
    binding.update(schema=clock.BINDING_SCHEMA,
                   presentation_start={"num": anchor, "den": 48000})
    return {"binding": binding, **artifacts}


def mutate(value, role, change):
    document = json.loads(value[role])
    change(document)
    value[role] = packed(document)
    value["binding"][role] = desc(value[role])


def omit(value, role):
    value[role] = None
    value["binding"][role] = None


def inspect(value):
    return clock.inspect_originals(**value)


class AacClockInspectionTests(unittest.TestCase):
    def test_bound_non1024_geometry_is_consistent_without_native_authority(self):
        value = fixture()
        result = inspect(value)
        self.assertEqual(result["status"], "CONSISTENT_WITHIN_SCOPE")
        trim = result["sections"]["explicit_trimming_reconciliation"]
        self.assertEqual(trim["observed_removed_start_samples"], 240)
        self.assertEqual(trim["observed_removed_end_samples"], 80)
        self.assertFalse(trim["fixed_codec_frame_size_assumed"])
        self.assertFalse(result["native_execution_attested"])
        self.assertFalse(result["physical_AV_master_acceptance"])
        self.assertEqual(result["original_capture_authentication"], "EXTERNAL_CUSTODY_REQUIRED")

    def test_last_packet_duration_is_not_reduced_twice(self):
        value = fixture()
        packets = json.loads(value["packets"])["packets"]
        manual = json.loads(value["manual_frames"])["frames"]
        self.assertEqual(packets[-1]["duration"], 480)
        self.assertEqual(manual[-1]["nb_samples"], 560)
        self.assertEqual(inspect(value)["status"], "CONSISTENT_WITHIN_SCOPE")

    def test_nonzero_source_anchor_is_explicit_and_preserved(self):
        result = inspect(fixture(anchor=240))
        self.assertEqual(result["status"], "CONSISTENT_WITHIN_SCOPE")
        self.assertEqual(result["source_presentation_start"], {"num": "1", "den": "200"})

    def test_skip_can_span_more_than_one_observed_frame(self):
        result = inspect(fixture(head=600))
        self.assertEqual(result["status"], "CONSISTENT_WITHIN_SCOPE")
        self.assertEqual(result["sections"]["explicit_trimming_reconciliation"]
                         ["observed_removed_start_samples"], 600)

    def test_discard_flag_and_skip_control_are_not_counted_twice(self):
        result = inspect(fixture(head=480, first_discard=True))
        self.assertEqual(result["status"], "CONSISTENT_WITHIN_SCOPE")
        trim = result["sections"]["explicit_trimming_reconciliation"]
        self.assertEqual(trim["observed_discard_flag_samples"], 480)
        self.assertEqual(trim["observed_removed_start_samples"], 0)

    def test_packet_document_alone_never_proves_decoder_trimming(self):
        value = fixture()
        omit(value, "default_frames")
        omit(value, "manual_frames")
        result = inspect(value)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["sections"]["packet_and_declared_stream_clock"]["status"], "PASS")
        self.assertFalse(result["physical_AV_master_acceptance"])

    def test_default_frames_without_manual_observation_are_insufficient(self):
        value = fixture()
        omit(value, "manual_frames")
        self.assertEqual(inspect(value)["status"], "INSUFFICIENT_EVIDENCE")

    def test_absent_side_data_is_not_an_explicit_zero(self):
        value = fixture(head=0, tail=0)
        mutate(value, "packets", lambda d: [p.pop("side_data_list", None)
                                          for p in d["packets"]])
        result = inspect(value)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("no-explicit-skip-or-discard-control",
                      result["sections"]["explicit_trimming_reconciliation"]["findings"])

    def test_source_pcm_oracle_detects_muted_right_tail(self):
        value = fixture()
        raw = bytearray(value["decoded_pcm"])
        for index in range(len(raw) // 4 - 253, len(raw) // 4):
            raw[4 * index + 2:4 * index + 4] = b"\0\0"
        value["decoded_pcm"] = bytes(raw)
        value["binding"]["decoded_pcm"] = desc(value["decoded_pcm"])
        result = inspect(value)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["sections"]["source_pcm_content"]["status"], "FAIL")
        self.assertEqual(result["sections"]["default_decoder_frame_clock"]["status"], "PASS")

    def test_raw_pcm_length_cannot_replace_decoded_frame_clock(self):
        value = fixture()
        value["decoded_pcm"] += b"\0\0\0\0"
        value["binding"]["decoded_pcm"] = desc(value["decoded_pcm"])
        result = inspect(value)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("frame-samples-do-not-match-retained-pcm-length",
                      result["sections"]["default_decoder_frame_clock"]["findings"])

    def test_default_clock_gap_is_detected(self):
        value = fixture()
        def change(document):
            document["frames"][2]["pts"] += 1
            document["frames"][2]["best_effort_timestamp"] += 1
        mutate(value, "default_frames", change)
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_packet_duration_gap_is_detected(self):
        value = fixture()
        def change(document):
            packet = document["packets"][1]
            packet["duration"] -= 1
            packet["duration_time"] = seconds(packet["duration"])
        mutate(value, "packets", change)
        result = inspect(value)
        self.assertIn("packet-presentation-gap-or-overlap",
                      result["sections"]["packet_and_declared_stream_clock"]["findings"])

    def test_reordered_packets_are_not_sorted_into_acceptance(self):
        value = fixture()
        def change(document):
            document["packets"][1:3] = reversed(document["packets"][1:3])
        mutate(value, "packets", change)
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_stream_duration_mismatch_is_detected(self):
        value = fixture()
        def change(document):
            stream = document["streams"][0]
            stream["duration_ts"] += 1
            stream["duration"] = seconds(stream["duration_ts"])
        mutate(value, "packets", change)
        omit(value, "default_frames")
        omit(value, "manual_frames")
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_unassociated_manual_frame_cannot_prove_trimming(self):
        value = fixture()
        mutate(value, "manual_frames", lambda d: d["frames"][1].update(pkt_size="97"))
        result = inspect(value)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")

    def test_missing_manual_packet_frame_is_insufficient(self):
        value = fixture()
        mutate(value, "manual_frames", lambda d: d["frames"].pop(0))
        self.assertEqual(inspect(value)["status"], "INSUFFICIENT_EVIDENCE")

    def test_duplicate_frame_position_cannot_be_a_positive_witness(self):
        value = fixture()
        mutate(value, "manual_frames",
               lambda d: d["frames"][1].update(pkt_pos=d["frames"][0]["pkt_pos"]))
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_oversized_discard_is_not_silently_ignored(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][-1]["side_data_list"][0]
               .update(discard_padding=2000))
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_unconsumed_skip_is_rejected(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][0]["side_data_list"][0]
               .update(skip_samples=100000))
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_changed_byte_binding_is_rejected_before_analysis(self):
        value = fixture()
        value["packets"] += b" "
        with self.assertRaisesRegex(clock.AacClockRejected, "packets-bytes-changed"):
            inspect(value)

    def test_duplicate_json_keys_are_rejected(self):
        value = fixture()
        value["packets"] = b'{"packets":[],"packets":[],"streams":[]}'
        value["binding"]["packets"] = desc(value["packets"])
        with self.assertRaises(clock.AacClockRejected):
            inspect(value)

    def test_nonfinite_json_is_rejected_even_in_an_extra_field(self):
        value = fixture()
        value["packets"] = value["packets"][:-1] + b',"extra":NaN}'
        value["binding"]["packets"] = desc(value["packets"])
        with self.assertRaises(clock.AacClockRejected):
            inspect(value)

    def test_exponent_overflow_in_ignored_metadata_is_rejected(self):
        value = fixture()
        value["packets"] = value["packets"][:-1] + b',"extra":1e309}'
        value["binding"]["packets"] = desc(value["packets"])
        with self.assertRaisesRegex(clock.AacClockRejected, "nonfinite"):
            inspect(value)

    def test_boolean_timestamp_is_not_an_integer(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][0].update(pts=True))
        with self.assertRaises(clock.AacClockRejected):
            inspect(value)

    def test_missing_pts_is_not_reconstructed_from_best_effort(self):
        value = fixture()
        mutate(value, "default_frames", lambda d: d["frames"][0].pop("pts"))
        with self.assertRaises(clock.AacClockRejected):
            inspect(value)

    def test_duplicate_skip_records_are_rejected(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][0]["side_data_list"].append(
            copy.deepcopy(d["packets"][0]["side_data_list"][0])))
        with self.assertRaisesRegex(clock.AacClockRejected, "duplicate-skip"):
            inspect(value)

    def test_skip_count_must_fit_ffmpeg_u32(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][0]["side_data_list"][0]
               .update(skip_samples=2**32))
        with self.assertRaises(clock.AacClockRejected):
            inspect(value)

    def test_unsupported_side_data_reason_is_explicitly_rejected(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][0]["side_data_list"][0]
               .update(skip_reason=2))
        with self.assertRaisesRegex(clock.AacClockRejected, "reason-unsupported"):
            inspect(value)

    def test_foreign_audio_stream_is_rejected(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][1].update(stream_index=9))
        with self.assertRaisesRegex(clock.AacClockRejected, "packet-stream-index"):
            inspect(value)

    def test_packet_byte_range_outside_bound_master_is_rejected(self):
        value = fixture()
        mutate(value, "packets", lambda d: d["packets"][0].update(pos="9999999"))
        with self.assertRaisesRegex(clock.AacClockRejected, "outside-master"):
            inspect(value)

    def test_off_grid_source_anchor_is_not_rounded(self):
        value = fixture()
        value["binding"]["presentation_start"] = {"num": 1, "den": 47999}
        with self.assertRaisesRegex(clock.AacClockRejected, "not-on-sample-grid"):
            inspect(value)

    def test_default_and_manual_captures_cannot_be_swapped(self):
        value = fixture()
        value["default_frames"], value["manual_frames"] = (
            value["manual_frames"], value["default_frames"])
        value["binding"]["default_frames"] = desc(value["default_frames"])
        value["binding"]["manual_frames"] = desc(value["manual_frames"])
        self.assertEqual(inspect(value)["status"], "FAIL")

    def test_metadata_claims_cannot_create_native_authority(self):
        value = fixture()
        mutate(value, "packets", lambda d: d.update(native_execution_attested=True,
                                                  human_acceptance=True))
        result = inspect(value)
        self.assertFalse(result["native_execution_attested"])
        self.assertFalse(result["human_acceptance"])

    def test_missing_optional_original_cannot_keep_a_hash_claim(self):
        value = fixture()
        value["manual_frames"] = None
        with self.assertRaisesRegex(clock.AacClockRejected, "missing-bound-original"):
            inspect(value)

    def test_inspection_preserves_every_original_input(self):
        value = fixture()
        before = copy.deepcopy(value)
        inspect(value)
        self.assertEqual(value, before)


if __name__ == "__main__":
    unittest.main()
