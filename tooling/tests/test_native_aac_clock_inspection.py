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




def presentation_fixture(*, end_discard=False, source_count=4800, mp4_changes=None):
    """Synthetic MP4 tables and PCM geometry only; no encoded AAC or G2 proof."""
    if __package__:
        from .test_native_mp4_aac_presentation import fixture as mp4_fixture, box
    else:
        from test_native_mp4_aac_presentation import fixture as mp4_fixture, box
    assert source_count % 48 == 0
    packet_count = (source_count + 1024 + 1023) // 1024
    final_delta = source_count + 1024 - (packet_count - 1) * 1024
    padding = 1024 - final_delta
    source_pcm = b"".join(struct.pack(
        "<hh", round(8000 * math.sin(2 * math.pi * 431 * i / 48000)),
        round(9000 * math.sin(2 * math.pi * 713 * i / 48000)))
        for i in range(source_count))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(48000)
        wav.writeframes(source_pcm)
    params = dict(segment=source_count // 48, movie_duration=source_count // 48,
                  media_duration=source_count + 1024, packet_count=packet_count,
                  counts=((packet_count - 1, 1024), (1, final_delta)))
    params.update(mp4_changes or {})
    master = mp4_fixture(**params) + box(b"mdat", bytes(packet_count * 128 + 1024))
    header = dict(index=1, codec_name="aac", codec_type="audio", sample_rate="48000",
        channels=2, time_base="1/48000", start_pts=0, start_time=seconds(0),
        duration_ts=source_count, duration=seconds(source_count), nb_frames=str(packet_count),
        nb_read_packets=str(packet_count), initial_padding=0)
    packets, manual, default = [], [], []
    for index in range(packet_count):
        point, position = (index - 1) * 1024, 512 + index * 128
        duration = final_delta if index == packet_count - 1 else 1024
        packet = dict(codec_type="audio", stream_index=1, pts=point, dts=point,
            duration=duration, pts_time=seconds(point), dts_time=seconds(point),
            duration_time=seconds(duration), pos=str(position), size="96",
            flags="KD_" if index == 0 else "K__")
        if index == 0 or (end_discard and index == packet_count - 1):
            packet["side_data_list"] = [dict(side_data_type="Skip Samples",
                skip_samples=1024 if index == 0 else 0,
                discard_padding=padding if index != 0 else 0, skip_reason=0, discard_reason=0)]
        frame = dict(media_type="audio", stream_index=1, pts=point, nb_samples=1024,
            channels=2, pkt_pos=str(position), pkt_size="96", sample_fmt="fltp",
            best_effort_timestamp=point, duration=duration)
        manual.append(frame)
        if index:
            shown = dict(frame)
            if end_discard and index == packet_count - 1:
                shown["nb_samples"] = final_delta
            default.append(shown)
        packets.append(packet)
    artifacts = dict(original_wav=stream.getvalue(), master=master,
        packets=packed(dict(packets=packets, streams=[header])),
        decoded_pcm=source_pcm + (b"" if end_discard else bytes(padding * 4)),
        default_frames=packed(dict(frames=default, streams=[header])),
        manual_frames=packed(dict(frames=manual, streams=[header])))
    binding = {role: desc(blob) for role, blob in artifacts.items()}
    binding.update(schema=clock.BINDING_SCHEMA, presentation_start={"num": 0, "den": 1})
    return dict(binding=binding, **artifacts)


class AacPresentationReconciliationTests(unittest.TestCase):
    def test_container_tail_is_reconciled_without_replacing_legacy_failure(self):
        value = presentation_fixture()
        historical_before = packed(clock.inspect_originals(**value))
        self.assertEqual(json.loads(historical_before)["status"], "FAIL")
        result = clock.inspect_presentation_originals(**value)
        self.assertEqual(result["status"], "CONSISTENT_WITHIN_SCOPE")
        self.assertEqual(result["historical_clock_report_status"], "FAIL")
        self.assertFalse(result["historical_clock_report_replaced"])
        self.assertEqual(result["source_sample_frames"], 4800)
        self.assertEqual(result["retained_decoded_sample_frames"], 5120)
        self.assertEqual(result["unpresented_access_unit_tail_samples"], 320)
        self.assertEqual(result["observed_decoder_trimming"]["observed_discard_flag_samples"], 1024)
        self.assertEqual(packed(clock.inspect_originals(**value)), historical_before)
        for key in ("input_bytes_changed", "media_reencoded_or_trimmed", "native_execution_attested",
                    "native_operation_started", "physical_AV_master_acceptance", "release_authorized"):
            self.assertFalse(result[key])

    def test_observed_terminal_discard_is_supported_without_double_subtraction(self):
        value = presentation_fixture(end_discard=True)
        result = clock.inspect_presentation_originals(**value)
        self.assertEqual(result["retained_decoded_sample_frames"], 4800)
        self.assertEqual(result["retained_pcm_samples_outside_presentation"], 0)
        self.assertEqual(result["unpresented_access_unit_tail_samples"], 320)
        self.assertEqual(result["observed_decoder_trimming"]["observed_removed_end_samples"], 320)

    def test_inert_elst_stts_and_sample_count_changes_are_rejected(self):
        for changes in (dict(priming=1025),
                        dict(counts=((5, 1024), (1, 703))),
                        dict(packet_count=7)):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(clock.AacClockRejected, "presentation-container-"):
                    clock.inspect_presentation_originals(**presentation_fixture(mp4_changes=changes))

    def test_packet_document_count_cannot_override_original_container(self):
        value = presentation_fixture()
        mutate(value, "packets", lambda document: document["packets"].pop())
        with self.assertRaisesRegex(clock.AacClockRejected, "presentation-original-packet"):
            clock.inspect_presentation_originals(**value)

    def test_missing_either_frame_mode_is_not_promoted(self):
        for role in ("default_frames", "manual_frames"):
            with self.subTest(role=role):
                value = presentation_fixture()
                omit(value, role)
                with self.assertRaisesRegex(clock.AacClockRejected, "both-original-frame-modes"):
                    clock.inspect_presentation_originals(**value)

    def test_unexplained_pcm_geometry_is_rejected_without_cutting_samples(self):
        value = presentation_fixture()
        value["decoded_pcm"] += bytes(4)
        value["binding"]["decoded_pcm"] = desc(value["decoded_pcm"])
        with self.assertRaisesRegex(clock.AacClockRejected, "presentation-container-"):
            clock.inspect_presentation_originals(**value)

    def test_source_content_failure_is_not_excused_by_container_padding(self):
        value = presentation_fixture()
        value["decoded_pcm"] = bytes(len(value["decoded_pcm"]))
        value["binding"]["decoded_pcm"] = desc(value["decoded_pcm"])
        with self.assertRaisesRegex(clock.AacClockRejected, "source-content-not-verified"):
            clock.inspect_presentation_originals(**value)

    def test_default_frame_gap_and_manual_packet_mismatch_are_rejected(self):
        value = presentation_fixture()
        mutate(value, "default_frames",
               lambda document: document["frames"][1].update(pts=1025, best_effort_timestamp=1025))
        with self.assertRaisesRegex(clock.AacClockRejected, "default-frame-geometry"):
            clock.inspect_presentation_originals(**value)
        value = presentation_fixture()
        mutate(value, "manual_frames",
               lambda document: document["frames"][1].update(pkt_pos="641"))
        with self.assertRaisesRegex(clock.AacClockRejected, "decode-trimming-not-reconciled"):
            clock.inspect_presentation_originals(**value)

    def test_nonzero_source_origin_remains_explicitly_unsupported(self):
        value = presentation_fixture()
        value["binding"]["presentation_start"] = {"num": 1, "den": 48000}
        with self.assertRaisesRegex(clock.AacClockRejected, "nonzero-origin-unsupported"):
            clock.inspect_presentation_originals(**value)


if __name__ == "__main__":
    unittest.main()
