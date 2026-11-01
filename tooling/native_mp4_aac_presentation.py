"""Bounded, read-only presentation-clock check for an unfragmented AAC MP4.

The caller authenticates the original container and decodes its actual audio.
This check does not trim samples, infer content from packet durations, or grant
native acceptance. Unsupported edit layouts fail closed.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import struct

MAX_BYTES = 256 * 1024 * 1024
MAX_BOXES = 4096
MAX_PACKETS = 1_000_000
AAC_FRAME_SAMPLES = 1024


class PresentationClockError(ValueError):
    """The original container does not prove the selected source interval."""


def require(condition, code):
    if not condition:
        raise PresentationClockError(code)


class _Reader:
    def __init__(self, data):
        require(type(data) is bytes and 8 <= len(data) <= MAX_BYTES,
                "mp4_byte_budget")
        self.data = data
        self.box_count = 0

    def integer(self, fmt, offset, end):
        size = struct.calcsize(fmt)
        require(0 <= offset <= end - size <= len(self.data) - size,
                "mp4_truncated_field")
        return struct.unpack_from(fmt, self.data, offset)[0]

    def boxes(self, start, end):
        rows = []
        while start < end:
            self.box_count += 1
            require(self.box_count <= MAX_BOXES, "mp4_box_budget")
            require(end - start >= 8, "mp4_truncated_box")
            size = self.integer(">I", start, end)
            kind = self.data[start + 4:start + 8]
            header = 8
            if size == 1:
                size = self.integer(">Q", start + 8, end)
                header = 16
            elif size == 0:
                size = end - start
            require(header <= size <= end - start, "mp4_box_outside_parent")
            rows.append((kind, start + header, start + size))
            start += size
        require(start == end, "mp4_box_end_mismatch")
        return rows

    @staticmethod
    def one(rows, kind):
        selected = [row for row in rows if row[0] == kind]
        require(len(selected) == 1, "mp4_missing_or_duplicate_" + kind.decode("ascii"))
        return selected[0]

    def children(self, box):
        return self.boxes(box[1], box[2])

    def full(self, box, versions=(0,)):
        _, start, end = box
        word = self.integer(">I", start, end)
        version = word >> 24
        require(version in versions and word & 0xFFFFFF == 0,
                "mp4_unsupported_full_box")
        return start, end, version

    def clock(self, box):
        start, end, version = self.full(box, (0, 1))
        offset = start + (12 if version == 0 else 20)
        timescale = self.integer(">I", offset, end)
        duration = self.integer(">I" if version == 0 else ">Q", offset + 4, end)
        require(timescale > 0 and duration > 0, "mp4_invalid_clock")
        return timescale, duration

    def handler(self, box):
        start, end, _ = self.full(box)
        require(end - start >= 12, "mp4_truncated_handler")
        return self.data[start + 8:start + 12]


def inspect_mp4_aac_presentation(data, *, source_frames, decoded_frames,
                                 sample_rate=48000):
    """Check integer container clocks against actual source/decoded geometry.

    The source starts at zero at unit speed. One nonempty edit, one stereo
    48 kHz mp4a track and 1024-sample AAC access units are the supported profile.
    A decoder may expose the complete final access unit or the exact presented
    interval; its observed length must match one of those explicit geometries.
    """
    require(type(source_frames) is int and 0 < source_frames <= MAX_PACKETS * 1024,
            "source_frame_count_invalid")
    require(type(decoded_frames) is int and decoded_frames > 0,
            "decoded_frame_count_invalid")
    require(type(sample_rate) is int and sample_rate == 48000,
            "unsupported_source_sample_rate")
    reader = _Reader(data)
    top = reader.boxes(0, len(data))
    require(not any(row[0] == b"moof" for row in top), "fragmented_mp4_unsupported")
    movie = reader.children(reader.one(top, b"moov"))
    require(not any(row[0] == b"mvex" for row in movie), "fragmented_mp4_unsupported")
    movie_scale, movie_duration = reader.clock(reader.one(movie, b"mvhd"))
    tracks = [row for row in movie if row[0] == b"trak"]
    require(1 <= len(tracks) <= 8, "mp4_track_budget")
    audio = []
    for track in tracks:
        children = reader.children(track)
        media = reader.children(reader.one(children, b"mdia"))
        handler = reader.handler(reader.one(media, b"hdlr"))
        if handler == b"soun":
            audio.append((children, media))
    require(len(audio) == 1, "mp4_audio_track_inventory")
    track, media = audio[0]
    clock, duration = reader.clock(reader.one(media, b"mdhd"))
    require(clock == sample_rate, "mp4_audio_clock_differs_from_source")
    edits = reader.children(reader.one(track, b"edts"))
    start, end, version = reader.full(reader.one(edits, b"elst"), (0, 1))
    require(reader.integer(">I", start + 4, end) == 1, "mp4_edit_inventory")
    width = 4 if version == 0 else 8
    require(end - start == 8 + 2 * width + 4, "mp4_edit_size")
    segment = reader.integer(">I" if width == 4 else ">Q", start + 8, end)
    priming = reader.integer(">i" if width == 4 else ">q", start + 8 + width, end)
    rate = reader.integer(">I", start + 8 + 2 * width, end)
    require(rate == 0x00010000, "mp4_edit_rate_not_unit")
    require(0 <= priming <= 2 * AAC_FRAME_SAMPLES, "mp4_empty_or_unbounded_edit")
    presented = Fraction(segment * clock, movie_scale)
    require(presented == source_frames, "mp4_presentation_differs_from_source")
    require(Fraction(movie_duration * clock, movie_scale) == source_frames,
            "mp4_movie_duration_differs_from_source")

    info = reader.children(reader.one(media, b"minf"))
    table = reader.children(reader.one(info, b"stbl"))
    require(not any(row[0] == b"ctts" for row in table), "mp4_audio_composition_offsets_unsupported")
    start, end, _ = reader.full(reader.one(table, b"stsd"))
    require(reader.integer(">I", start + 4, end) == 1, "mp4_audio_description_inventory")
    entries = reader.boxes(start + 8, end)
    require(len(entries) == 1 and entries[0][0] == b"mp4a", "mp4_audio_sample_entry")
    _, entry, entry_end = entries[0]
    require(entry_end - entry >= 28, "mp4_audio_sample_entry_truncated")
    require(reader.integer(">H", entry + 8, entry_end) == 0,
            "mp4_audio_sample_entry_version")
    require(reader.integer(">H", entry + 16, entry_end) == 2,
            "mp4_audio_channel_count")
    require(reader.integer(">I", entry + 24, entry_end) == sample_rate << 16,
            "mp4_audio_sample_entry_rate")

    start, end, _ = reader.full(reader.one(table, b"stts"))
    count = reader.integer(">I", start + 4, end)
    require(1 <= count <= 2 and end - start == 8 + count * 8,
            "mp4_aac_timing_inventory")
    timing = [(reader.integer(">I", start + 8 + index * 8, end),
               reader.integer(">I", start + 12 + index * 8, end))
              for index in range(count)]
    require(all(n > 0 and 0 < delta <= AAC_FRAME_SAMPLES for n, delta in timing),
            "mp4_aac_timing_range")
    require(timing[0][1] == AAC_FRAME_SAMPLES
            and (count == 1 or timing[-1][0] == 1), "mp4_aac_partial_frame_not_terminal")
    packet_count = sum(n for n, _ in timing)
    ticks = sum(n * delta for n, delta in timing)
    require(0 < packet_count <= MAX_PACKETS, "mp4_aac_packet_budget")
    start, end, _ = reader.full(reader.one(table, b"stsz"))
    fixed_size = reader.integer(">I", start + 4, end)
    size_count = reader.integer(">I", start + 8, end)
    require(size_count == packet_count, "mp4_aac_packet_count_mismatch")
    require(end - start == 12 + (0 if fixed_size else 4 * size_count),
            "mp4_aac_sample_size_table")
    if not fixed_size:
        require(all(reader.integer(">I", start + 12 + index * 4, end) > 0
                    for index in range(size_count)), "mp4_aac_empty_packet")
    require(duration == ticks, "mp4_audio_duration_disagrees_with_packets")
    require(ticks - priming == source_frames, "mp4_audio_media_interval_differs_from_source")
    final_delta = timing[-1][1]
    unpresented_tail = AAC_FRAME_SAMPLES - final_delta
    complete_decode = packet_count * AAC_FRAME_SAMPLES - priming
    require(complete_decode == source_frames + unpresented_tail,
            "mp4_aac_decode_geometry_inconsistent")
    require(decoded_frames in {source_frames, complete_decode},
            "decoded_audio_interval_not_explained_by_container")
    return {
        "schema": "motionwright.native.mp4-aac-presentation/1",
        "master_sha256": hashlib.sha256(data).hexdigest(),
        "source_sample_frames": source_frames,
        "decoded_sample_frames": decoded_frames,
        "presented_sample_frames": int(presented),
        "sample_rate": sample_rate,
        "channels": 2,
        "aac_packet_count": packet_count,
        "aac_media_ticks": ticks,
        "initial_edit_skip_samples": priming,
        "final_packet_duration_samples": final_delta,
        "unpresented_final_access_unit_samples": unpresented_tail,
        "observed_decoded_samples_after_presentation": decoded_frames - source_frames,
        "presentation_start_sample": 0,
        "presentation_end_sample": source_frames,
        "source_interval_exact": True,
        "samples_trimmed_or_reencoded": False,
        "sample_content_proven_by_this_clock_check": False,
        "native_host_or_provider_attested_here": False,
    }
