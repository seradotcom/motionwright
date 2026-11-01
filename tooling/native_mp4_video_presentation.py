"""Exact presentation intervals for a bounded, unfragmented H.264 MP4 track.

This inspects authenticated container timing, not decoded pixels or Native Host
authority. Decode order may differ from presentation order: ctts v0 and v1 are
applied before checking the source frame grid and final presentation endpoint.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
import re

from native_mp4_aac_presentation import MAX_BYTES, _Reader, require

MAX_VIDEO_FRAMES = 100_000


def _ratio(value):
    value = Fraction(value)
    return {"num": value.numerator, "den": value.denominator}


def _timing(reader, box, expected_frames, *, composition=False):
    start, end, version = reader.full(box, (0, 1) if composition else (0,))
    count = reader.integer(">I", start + 4, end)
    require(1 <= count <= expected_frames and end - start == 8 + count * 8,
            "mp4_video_timing_table_size")
    runs = []
    samples = 0
    for index in range(count):
        length = reader.integer(">I", start + 8 + index * 8, end)
        value = reader.integer(">i" if composition and version == 1 else ">I",
                               start + 12 + index * 8, end)
        require(length > 0 and (composition or value > 0), "mp4_video_timing_range")
        samples += length
        require(samples <= expected_frames, "mp4_video_timing_sample_budget")
        runs.append((length, value))
    require(samples == expected_frames, "mp4_video_timing_sample_count")
    return version, [value for length, value in runs for _ in range(length)]


def inspect_mp4_video_presentation(data, *, expected_frames=180, fps_num=30, fps_den=1):
    """Prove each source frame's exact presented interval from integer tables.

    Supported profile: one enabled H.264 video track, one nonempty unit-speed
    edit, a fixed source frame grid, and an unfragmented sample table. AAC and
    decoded pixel/content checks remain separate mandatory consumer checks.
    """
    require(type(expected_frames) is int and 0 < expected_frames <= MAX_VIDEO_FRAMES,
            "mp4_video_expected_frame_count")
    require(type(fps_num) is int and type(fps_den) is int
            and 0 < fps_num <= 1_000_000 and 0 < fps_den <= 1_000_000,
            "mp4_video_source_frame_rate")
    period = Fraction(fps_den, fps_num)
    source_end = expected_frames * period
    reader = _Reader(data)
    top = reader.boxes(0, len(data))
    require(not any(box[0] == b"moof" for box in top), "mp4_video_fragmented_unsupported")
    movie = reader.children(reader.one(top, b"moov"))
    require(not any(box[0] == b"mvex" for box in movie), "mp4_video_fragmented_unsupported")
    movie_scale, movie_duration = reader.clock(reader.one(movie, b"mvhd"))
    require(Fraction(movie_duration, movie_scale) == source_end,
            "mp4_video_movie_duration_differs_from_source")
    tracks = [box for box in movie if box[0] == b"trak"]
    require(1 <= len(tracks) <= 8, "mp4_video_track_budget")
    videos = []
    for track in tracks:
        children = reader.children(track)
        media = reader.children(reader.one(children, b"mdia"))
        if reader.handler(reader.one(media, b"hdlr")) == b"vide":
            videos.append((children, media))
    require(len(videos) == 1, "mp4_video_track_inventory")
    track, media = videos[0]
    _, start, end = reader.one(track, b"tkhd")
    word = reader.integer(">I", start, end)
    version, flags = word >> 24, word & 0xFFFFFF
    require(version in (0, 1) and flags & ~7 == 0 and flags & 3 == 3,
            "mp4_video_track_disabled_or_unsupported")
    require(end - start >= (84 if version == 0 else 96), "mp4_video_track_header_truncated")
    track_id = reader.integer(">I", start + (12 if version == 0 else 20), end)
    track_duration = reader.integer(">I" if version == 0 else ">Q",
                                     start + (20 if version == 0 else 28), end)
    require(track_id > 0 and Fraction(track_duration, movie_scale) == source_end,
            "mp4_video_track_duration_differs_from_source")
    media_scale, media_duration = reader.clock(reader.one(media, b"mdhd"))
    edits = reader.children(reader.one(track, b"edts"))
    start, end, version = reader.full(reader.one(edits, b"elst"), (0, 1))
    width = 4 if version == 0 else 8
    require(reader.integer(">I", start + 4, end) == 1, "mp4_video_edit_inventory")
    require(end - start == 8 + 2 * width + 4, "mp4_video_edit_size")
    edit_duration = reader.integer(">I" if width == 4 else ">Q", start + 8, end)
    media_time = reader.integer(">i" if width == 4 else ">q", start + 8 + width, end)
    rate = reader.integer(">I", start + 8 + 2 * width, end)
    require(rate == 0x00010000 and media_time >= 0, "mp4_video_edit_not_nonempty_unit_speed")
    require(Fraction(edit_duration, movie_scale) == source_end,
            "mp4_video_edit_duration_differs_from_source")

    info = reader.children(reader.one(media, b"minf"))
    table = reader.children(reader.one(info, b"stbl"))
    require(not any(box[0] == b"cslg" for box in table), "mp4_video_composition_shift_unsupported")
    start, end, _ = reader.full(reader.one(table, b"stsd"))
    require(reader.integer(">I", start + 4, end) == 1, "mp4_video_description_inventory")
    entries = reader.boxes(start + 8, end)
    require(len(entries) == 1 and entries[0][0] in (b"avc1", b"avc3")
            and entries[0][2] - entries[0][1] >= 78, "mp4_video_sample_entry")
    _, durations = _timing(reader, reader.one(table, b"stts"), expected_frames)
    require(sum(durations) == media_duration, "mp4_video_media_duration_disagrees_with_samples")
    composition = [box for box in table if box[0] == b"ctts"]
    require(len(composition) <= 1, "mp4_video_duplicate_composition_table")
    composition_version, offsets = (_timing(reader, composition[0], expected_frames, composition=True)
                                    if composition else (None, [0] * expected_frames))
    start, end, _ = reader.full(reader.one(table, b"stsz"))
    fixed_size = reader.integer(">I", start + 4, end)
    size_count = reader.integer(">I", start + 8, end)
    require(size_count == expected_frames, "mp4_video_sample_size_count")
    require(end - start == 12 + (0 if fixed_size else 4 * size_count), "mp4_video_sample_size_table")
    if not fixed_size:
        require(all(reader.integer(">I", start + 12 + index * 4, end) > 0
                    for index in range(size_count)), "mp4_video_empty_sample")

    dts, samples = 0, []
    for index, (duration, offset) in enumerate(zip(durations, offsets)):
        samples.append((dts + offset - media_time, duration, index))
        dts += duration
    ordered = sorted(samples)
    for index, (cts, duration, _) in enumerate(ordered):
        require(Fraction(cts, media_scale) == index * period,
                "mp4_video_presentation_grid_gap_overlap_or_shift")
        require(Fraction(duration, media_scale) == period,
                "mp4_video_presented_frame_duration_differs_from_source")
    require(Fraction(ordered[-1][0] + ordered[-1][1], media_scale) == source_end,
            "mp4_video_presentation_end_differs_from_source")
    return {
        "schema": "motionwright.native.mp4-video-presentation/1",
        "master_sha256": hashlib.sha256(data).hexdigest(),
        "source_video_frames": expected_frames,
        "frame_rate": {"num": fps_num, "den": fps_den},
        "video_track_id": track_id,
        "video_timescale": media_scale,
        "video_media_ticks": media_duration,
        "video_sample_count": expected_frames,
        "composition_time_version": composition_version,
        "initial_edit_media_time": media_time,
        "presentation_start": {"num": 0, "den": 1},
        "presentation_end": _ratio(source_end),
        "all_source_frame_intervals_exact": True,
        "decode_order_rearranged": [row[2] for row in ordered] != list(range(expected_frames)),
        "samples_decoded_or_reencoded": False,
        "sample_content_proven_by_this_clock_check": False,
        "native_host_or_provider_attested_here": False,
    }


def inspect_original_mp4_video_presentation(path, expected_sha256, **profile):
    """Read a size-bounded original and require its exact authenticated hash."""
    require(type(expected_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", expected_sha256),
            "mp4_video_expected_hash_invalid")
    path = Path(path)
    require(8 <= path.stat().st_size <= MAX_BYTES, "mp4_video_original_byte_budget")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES and hashlib.sha256(data).hexdigest() == expected_sha256,
            "mp4_video_original_hash_changed")
    return inspect_mp4_video_presentation(data, **profile)
