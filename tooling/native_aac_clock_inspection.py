"""Read-only clock diagnostics for retained AAC originals.

No process, decoder, filesystem write, renderer, retry, consent or receipt mint.
The caller authenticates capture provenance separately. Hash equality here
identifies supplied bytes; it does not authenticate who produced those bytes.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import math
import re

import native_evidence_projection as projection
from native_mlt_evidence_selection import PACKETS
from native_pcm_source_fidelity import (
    MAX_SOURCE_FRAMES, MAX_PADDING_FRAMES, PcmContentError, _source_wave,
    inspect_source_pcm16_stereo_content,
)

SCHEMA = "motionwright.native.aac-original-clock-inspection/1"
BINDING_SCHEMA = "motionwright.native.aac-original-clock-input/1"
MAX_MASTER = 256 * 1024 * 1024
MAX_PROBE = 1024 * 1024
MAX_ROWS = 512
SHA = re.compile(r"[0-9a-f]{64}\Z")
DECIMAL = re.compile(r"-?[0-9]+(?:\.[0-9]+)?\Z")
DIGITS = re.compile(r"(?:0|[1-9][0-9]*)\Z")
I64_MAX = 2**63 - 1
RATE = 48000


class AacClockRejected(ValueError):
    """An input is malformed or does not match its explicit byte binding."""


def require(ok, code):
    if not ok:
        raise AacClockRejected(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def integer(value, minimum=0, maximum=I64_MAX):
    require(type(value) is int and minimum <= value <= maximum, "integer-range")
    return value


def fraction_object(value):
    require(type(value) is dict and set(value) == {"num", "den"}, "rational-fields")
    n = integer(value["num"])
    d = integer(value["den"], 1)
    return Fraction(n, d)


def rational(value):
    return {"num": str(value.numerator), "den": str(value.denominator)}


def document(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_PROBE, "probe-byte-budget")
    try:
        value = projection.strict(raw)
    except (ValueError, OverflowError, RecursionError) as error:
        raise AacClockRejected("probe-json-invalid") from error
    def finite(item, depth=0):
        require(depth < 64, "probe-json-depth")
        if type(item) is float:
            require(math.isfinite(item), "probe-json-nonfinite")
        elif type(item) is dict:
            for child in item.values():
                finite(child, depth + 1)
        elif type(item) is list:
            for child in item:
                finite(child, depth + 1)
    finite(value)
    return value


def descriptor(raw, expected, maximum, role):
    require(type(expected) is dict and set(expected) == {"sha256", "bytes"},
            role + "-descriptor-fields")
    require(type(raw) is bytes and 0 < len(raw) <= maximum, role + "-byte-budget")
    require(type(expected["sha256"]) is str and SHA.fullmatch(expected["sha256"]),
            role + "-sha256")
    integer(expected["bytes"], 1, maximum)
    require(len(raw) == expected["bytes"] and sha(raw) == expected["sha256"],
            role + "-bytes-changed")
    return {"sha256": expected["sha256"], "bytes": expected["bytes"]}


def read_bindings(binding, originals):
    require(type(binding) is dict and set(binding) == {
        "schema", "presentation_start", "original_wav", "master", "packets",
        "decoded_pcm", "default_frames", "manual_frames",
    }, "binding-fields")
    require(binding["schema"] == BINDING_SCHEMA, "binding-schema")
    start = fraction_object(binding["presentation_start"])
    require((start * RATE).denominator == 1, "source-start-not-on-sample-grid")
    budgets = {
        "original_wav": MAX_SOURCE_FRAMES * 4 + 65536,
        "master": MAX_MASTER,
        "packets": MAX_PROBE,
        "decoded_pcm": (MAX_SOURCE_FRAMES + MAX_PADDING_FRAMES) * 4,
        "default_frames": MAX_PROBE,
        "manual_frames": MAX_PROBE,
    }
    observed = {}
    for role, maximum in budgets.items():
        raw = originals[role]
        expected = binding[role]
        if role in {"default_frames", "manual_frames"} and raw is None:
            require(expected is None, role + "-missing-bound-original")
            observed[role] = None
        else:
            require(expected is not None, role + "-binding-absent")
            observed[role] = descriptor(raw, expected, maximum, role)
    return start, observed


def displayed_time(value, exact, code):
    """Printed decimal seconds are cross-checks, never the source clock."""
    require(type(value) is str and len(value) <= 48 and DECIMAL.fullmatch(value),
            code + "-decimal")
    require(abs(Fraction(value) - exact) <= Fraction(1, 1000000),
            code + "-decimal-disagrees")


def stage(findings, **observations):
    return {"status": "FAIL" if findings else "PASS",
            "findings": list(dict.fromkeys(findings)), **observations}


def packet_clock(raw, master_bytes, source_start, source_frames):
    original = document(raw)
    try:
        view = projection.project(original, PACKETS)
    except (ValueError, OverflowError, RecursionError) as error:
        raise AacClockRejected("packet-schema") from error
    packets, stream = view["packets"], view["streams"][0]
    tb = Fraction(stream["time_base"])
    source_end = source_start + Fraction(source_frames, RATE)
    declared_start = stream["start_pts"] * tb
    declared_end = declared_start + stream["duration_ts"] * tb
    displayed_time(stream["start_time"], declared_start, "stream-start")
    displayed_time(stream["duration"], stream["duration_ts"] * tb, "stream-duration")
    findings = []
    if declared_start != source_start or declared_end != source_end:
        findings.append("declared-stream-interval-differs-from-source")
    if int(stream["nb_frames"]) != len(packets):
        findings.append("stream-packet-count-disagrees")
    if "nb_read_packets" in stream and int(stream["nb_read_packets"]) != len(packets):
        findings.append("observed-packet-count-disagrees")
    rows, controls = [], []
    previous_end = previous_dts = previous_file_end = None
    for index, packet in enumerate(packets):
        require(packet["stream_index"] == stream["index"], "packet-stream-index")
        pts, dts, duration = packet["pts"] * tb, packet["dts"] * tb, packet["duration"] * tb
        displayed_time(packet["pts_time"], pts, "packet-pts")
        displayed_time(packet["dts_time"], dts, "packet-dts")
        displayed_time(packet["duration_time"], duration, "packet-duration")
        if pts < dts:
            findings.append("packet-pts-before-dts")
        if previous_dts is not None and dts <= previous_dts:
            findings.append("packet-dts-not-strictly-increasing")
        if previous_end is not None and pts != previous_end:
            findings.append("packet-presentation-gap-or-overlap")
        position, size = int(packet["pos"]), int(packet["size"])
        require(size > 0 and position + size <= master_bytes, "packet-outside-master")
        if previous_file_end is not None and position < previous_file_end:
            findings.append("packet-file-ranges-overlap-or-reorder")
        side = packet.get("side_data_list", [])
        require(len(side) <= 1, "duplicate-skip-samples-side-data")
        control = side[0] if side else None
        if control is not None:
            for name in ("skip_samples", "discard_padding"):
                integer(control[name], 0, 2**32 - 1)
            for name in ("skip_reason", "discard_reason"):
                integer(control[name], 0, 255)
                require(control[name] in (0, 1), "skip-samples-reason-unsupported")
            controls.append({"packet_index": index, **control})
        rows.append({
            "index": index, "pts": pts, "end": pts + duration, "duration": duration,
            "pos": position, "size": size, "flags": packet["flags"], "control": control,
        })
        previous_end, previous_dts = pts + duration, dts
        previous_file_end = position + size
    if rows[0]["pts"] > source_start or rows[-1]["end"] < source_end:
        findings.append("packet-clock-does-not-cover-source-interval")
    section = stage(
        findings, time_base=rational(tb), stream_index=stream["index"],
        packet_count=len(rows), declared_start=rational(declared_start),
        declared_end=rational(declared_end), first_packet_pts=rational(rows[0]["pts"]),
        last_packet_end=rational(rows[-1]["end"]), explicit_controls=controls,
        initial_padding_metadata=stream.get("initial_padding"),
        rounded_seconds_used_as_clock=False,
        packet_duration_treated_as_decoded_sample_count=False,
        side_data_subtracted_from_packet_duration=False,
    )
    return section, rows, stream, tb


def frame_documents(raw, stream, tb, role, source_start, source_frames, pcm_frames):
    obj = document(raw)
    require(type(obj.get("frames")) is list and len(obj["frames"]) <= MAX_ROWS,
            role + "-frame-array")
    try:
        observed_streams = projection.project(
            obj.get("streams"), PACKETS.fields["streams"].rule)
    except (ValueError, OverflowError, RecursionError) as error:
        raise AacClockRejected(role + "-stream-schema") from error
    observed_stream = observed_streams[0]
    for key in ("index", "codec_name", "codec_type", "sample_rate", "channels",
                "time_base", "start_pts", "duration_ts"):
        require(observed_stream[key] == stream[key], role + "-stream-binding")
    fields = {
        "media_type": projection.Field(projection.Literal("audio")),
        "stream_index": projection.Field(projection.Number(0, I64_MAX, integer=True)),
        "pts": projection.Field(projection.Number(-I64_MAX, I64_MAX, integer=True)),
        "nb_samples": projection.Field(projection.Number(1, 65536, integer=True)),
        "channels": projection.Field(projection.Literal(2)),
        "pkt_pos": projection.Field(projection.String(24, pattern=r"[0-9]+")),
        "pkt_size": projection.Field(projection.String(24, pattern=r"[0-9]+")),
        "sample_fmt": projection.Field(projection.String(16, values=(
            "fltp", "flt", "s16", "s16p", "s32", "s32p", "dbl", "dblp"))),
        "best_effort_timestamp": projection.Field(
            projection.Number(-I64_MAX, I64_MAX, integer=True), required=False),
        "duration": projection.Field(
            projection.Number(0, I64_MAX, integer=True), required=False),
        "pkt_duration": projection.Field(
            projection.Number(0, I64_MAX, integer=True), required=False),
    }
    findings, rows, positions = [], [], set()
    previous_end = None
    total = 0
    for frame in obj["frames"]:
        try:
            value = projection.project(frame, projection.Object(fields))
        except (ValueError, OverflowError, RecursionError) as error:
            raise AacClockRejected(role + "-frame-schema") from error
        require(value["stream_index"] == stream["index"], role + "-frame-stream-index")
        point = value["pts"] * tb
        count = value["nb_samples"]
        require((point * RATE).denominator == 1, role + "-pts-off-sample-grid")
        position, size = int(value["pkt_pos"]), int(value["pkt_size"])
        require(size > 0, role + "-packet-size")
        if position in positions:
            findings.append("frame-packet-position-not-unique")
        positions.add(position)
        if previous_end is not None and point != previous_end:
            findings.append("decoded-frame-gap-or-overlap")
        if value.get("best_effort_timestamp", value["pts"]) != value["pts"]:
            findings.append("best-effort-pts-disagrees")
        rows.append({
            "pts": point, "samples": count, "end": point + Fraction(count, RATE),
            "pos": position, "size": size, "sample_fmt": value["sample_fmt"],
        })
        previous_end = rows[-1]["end"]
        total += count
    require(total <= MAX_SOURCE_FRAMES + RATE, role + "-total-sample-budget")
    if not rows:
        findings.append("no-decoded-audio-frames")
    if role == "default":
        if total != pcm_frames:
            findings.append("frame-samples-do-not-match-retained-pcm-length")
        if rows and (rows[0]["pts"] != source_start
                     or rows[-1]["end"] != source_start + Fraction(source_frames, RATE)
                     or total != source_frames):
            findings.append("decoded-sample-interval-differs-from-source")
    return stage(
        findings, frame_count=len(rows), sample_frames=total,
        first_pts=rational(rows[0]["pts"]) if rows else None,
        last_sample_end=rational(rows[-1]["end"]) if rows else None,
        integer_pts_and_sample_counts_used=True,
        packet_duration_not_used_as_sample_count=True,
    ), rows


def insufficient(code, **extra):
    return {"status": "INSUFFICIENT_EVIDENCE", "findings": [code], **extra}


def reconcile_trimming(packets, manual, default):
    """Compare observed default frames with bounded manual-mode frame geometry.

    This is not a decoder. One manual frame per original packet must be
    observed through position/size/PTS, not assumed from codec frame size.
    The capture of each decode mode must be authenticated by the caller.
    """
    explicit = [row for row in packets if row["control"] is not None]
    if not explicit:
        return insufficient("no-explicit-skip-or-discard-control",
                            absence_of_side_data_interpreted_as_zero=False)
    if len(manual) != len(packets):
        return insufficient("one-manual-frame-per-packet-not-observed",
                            manual_frames=len(manual), packets=len(packets))
    expected, findings = [], []
    pending = removed_skip = removed_padding = removed_flag = 0
    for packet, frame in zip(packets, manual):
        if (packet["pos"], packet["size"], packet["pts"]) != (
                frame["pos"], frame["size"], frame["pts"]):
            return insufficient("manual-frame-original-packet-association-not-observed")
        control = packet["control"]
        discard = 0
        if control is not None:
            # The frame side-data control replaces remaining skip state.
            # initial_padding is deliberately not added a second time.
            pending = control["skip_samples"]
            discard = control["discard_padding"]
        count, point = frame["samples"], frame["pts"]
        if "D" in packet["flags"]:
            removed_flag += count
            pending = max(0, pending - count)
            continue
        if pending >= count:
            removed_skip += count
            pending -= count
            continue
        if pending:
            removed_skip += pending
            point += Fraction(pending, RATE)
            count -= pending
            pending = 0
        # FFmpeg 6.1 does not apply oversized discard_padding to this frame.
        # An invalid or unsupported control never becomes a positive witness.
        if discard > count:
            findings.append("discard-padding-exceeds-observed-frame-remainder")
            continue
        removed_padding += discard
        count -= discard
        if count:
            expected.append((frame["pos"], frame["size"], point, count, frame["sample_fmt"]))
    if pending:
        findings.append("skip-control-remains-after-final-frame")
    actual = [(r["pos"], r["size"], r["pts"], r["samples"], r["sample_fmt"])
              for r in default]
    if expected != actual:
        findings.append("default-frames-do-not-match-observed-manual-trimming")
    return stage(
        findings, compared_default_frames=len(actual), compared_manual_frames=len(manual),
        observed_removed_start_samples=removed_skip,
        observed_removed_end_samples=removed_padding,
        observed_discard_flag_samples=removed_flag,
        initial_padding_added_to_controls=False,
        absence_of_side_data_interpreted_as_zero=False,
        fixed_codec_frame_size_assumed=False,
        encoded_sample_values_reconstructed=False,
        interpretation="FFMPEG_SKIP_DISCARD_FRAME_GEOMETRY_ONLY",
        encoder_delay_classification="NOT_INFERRED_FROM_SAMPLE_COUNTS",
        capture_mode_authentication="REQUIRED_FROM_ORIGINAL_CAPTURE_CUSTODY",
    )


def inspect_originals(*, binding, original_wav, master, packets, decoded_pcm,
                      default_frames=None, manual_frames=None):
    """Inspect immutable supplied bytes. No filesystem or subprocess access.

    A PASS subsection is an observation of the bound input documents. It is
    not an authenticated native invocation, an authorization, or an formal acceptance verdict.
    """
    start, identities = read_bindings(binding, {
        "original_wav": original_wav, "master": master, "packets": packets,
        "decoded_pcm": decoded_pcm, "default_frames": default_frames,
        "manual_frames": manual_frames,
    })
    try:
        source_frames, _ = _source_wave(original_wav)
    except PcmContentError as error:
        raise AacClockRejected("source-wave-profile-invalid") from error
    require(len(decoded_pcm) % 4 == 0, "decoded-pcm-not-whole-stereo-frames")
    pcm_frames = len(decoded_pcm) // 4
    container, packet_rows, stream, tb = packet_clock(
        packets, len(master), start, source_frames)
    try:
        witness = inspect_source_pcm16_stereo_content(
            original_wav, decoded_pcm,
            original_wav_sha256=identities["original_wav"]["sha256"],
            decoded_pcm_sha256=identities["decoded_pcm"]["sha256"])
        content = {"status": "PASS", "findings": [], "existing_content_witness": witness}
    except PcmContentError as error:
        content = {"status": "FAIL", "findings": ["existing-source-content-oracle-rejected"],
                   "existing_oracle_error": str(error)}
    default_rows = manual_rows = None
    if default_frames is None:
        default = insufficient("original-default-decoded-frame-document-missing")
    else:
        default, default_rows = frame_documents(
            default_frames, stream, tb, "default", start, source_frames, pcm_frames)
    if manual_frames is None:
        manual = insufficient("original-manual-decoded-frame-document-missing")
    else:
        manual, manual_rows = frame_documents(
            manual_frames, stream, tb, "manual", start, source_frames, pcm_frames)
    if default_rows is None or manual_rows is None:
        trimming = insufficient("both-observed-decode-mode-frame-documents-required")
    elif any(section["status"] != "PASS" for section in (container, default, manual)):
        trimming = insufficient("clock-or-frame-geometry-not-consistent")
    else:
        trimming = reconcile_trimming(packet_rows, manual_rows, default_rows)
    sections = {
        "packet_and_declared_stream_clock": container,
        "source_pcm_content": content,
        "default_decoder_frame_clock": default,
        "manual_decoder_frame_clock": manual,
        "explicit_trimming_reconciliation": trimming,
    }
    states = [section["status"] for section in sections.values()]
    status = ("FAIL" if "FAIL" in states else
              "INSUFFICIENT_EVIDENCE" if "INSUFFICIENT_EVIDENCE" in states else
              "CONSISTENT_WITHIN_SCOPE")
    return {
        "schema": SCHEMA, "status": status,
        "scope": "BOUND_48KHZ_STEREO_ORIGINAL_DOCUMENT_AND_PCM_DIAGNOSTIC",
        "originals": identities,
        "source_presentation_start": rational(start),
        "source_presentation_end": rational(start + Fraction(source_frames, RATE)),
        "source_sample_frames": source_frames,
        "retained_decoded_sample_frames": pcm_frames,
        "sections": sections,
        "input_bytes_changed": False, "native_operation_started": False,
        "native_operation_retried": False, "media_reencoded_or_trimmed": False,
        "original_capture_authentication": "EXTERNAL_CUSTODY_REQUIRED",
        "master_bytes_decoded_by_this_inspector": False,
        "native_execution_attested": False,
        "decoder_sample_values_reconstructed": False,
        "physical_AV_master_acceptance": False,
        "human_acceptance": False, "release_authorized": False,
    }
