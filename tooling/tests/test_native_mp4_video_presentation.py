"""Constructed BMFF clock tables, including B-frame ordering; never G2 proof."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mp4_video_presentation as video
from native_mp4_aac_presentation import PresentationClockError


def box(kind, payload):
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def full(kind, payload, version=0, flags=0):
    return box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def clock_header(kind, scale, duration, version=0):
    body = (struct.pack(">QQIQ", 0, 0, scale, duration) if version else
            struct.pack(">IIII", 0, 0, scale, duration))
    return full(kind, body + bytes(80 if kind == b"mvhd" else 4), version)


def video_track_fixture(*, frame_count=180, timescale=15360, sample_delta=512,
                        edit_duration=6000, media_time=1024, media_duration=None,
                        track_duration=6000, composition_runs=None, composition_version=0,
                        stts_runs=None, stsz_count=None, edits=None, flags=3,
                        header_version=0, sample_entry=b"avc1", fixed_size=8):
    """An inert trak for embedding in container-clock fixtures; no H.264 payload."""
    if media_duration is None:
        media_duration = frame_count * sample_delta
    if stts_runs is None:
        stts_runs = [(frame_count, sample_delta)]
    if composition_runs is None:
        composition_runs = [(frame_count, media_time)]
    if stsz_count is None:
        stsz_count = frame_count
    if edits is None:
        edits = [(edit_duration, media_time, 65536)]
    header = (struct.pack(">QQIIQ", 0, 0, 1, 0, track_duration) if header_version else
              struct.pack(">IIIII", 0, 0, 1, 0, track_duration))
    track = full(b"tkhd", header + bytes(60), header_version, flags)
    edit_data = struct.pack(">I", len(edits)) + b"".join(
        struct.pack(">QqI" if header_version else ">IiI", duration, origin, rate)
        for duration, origin, rate in edits)
    track += box(b"edts", full(b"elst", edit_data, header_version))
    sample = bytearray(78)
    struct.pack_into(">H", sample, 6, 1)
    struct.pack_into(">HH", sample, 24, 1920, 1080)
    table = full(b"stsd", struct.pack(">I", 1) + box(sample_entry, sample))
    table += full(b"stts", struct.pack(">I", len(stts_runs)) + b"".join(
        struct.pack(">II", length, delta) for length, delta in stts_runs))
    if composition_runs:
        table += full(b"ctts", struct.pack(">I", len(composition_runs)) + b"".join(
            struct.pack(">Ii" if composition_version == 1 else ">II", length, offset)
            for length, offset in composition_runs), composition_version)
    sizes = struct.pack(">II", fixed_size, stsz_count)
    if not fixed_size:
        sizes += struct.pack(">I", 8) * stsz_count
    table += full(b"stsz", sizes)
    media = clock_header(b"mdhd", timescale, media_duration, header_version)
    media += full(b"hdlr", bytes(4) + b"vide" + bytes(12))
    media += box(b"minf", box(b"stbl", table))
    return box(b"trak", track + box(b"mdia", media))


def fixture(*, movie_scale=1000, movie_duration=6000, duplicate_video=False,
            fragmented=False, **kwargs):
    track = video_track_fixture(**kwargs)
    movie = clock_header(b"mvhd", movie_scale, movie_duration, kwargs.get("header_version", 0))
    movie += track * (2 if duplicate_video else 1)
    if fragmented:
        movie += box(b"mvex", b"")
    return box(b"ftyp", b"isom" + bytes(4) + b"isom") + box(b"moov", movie)


class Mp4VideoPresentationTests(unittest.TestCase):
    def test_authenticates_clock_result_without_claiming_decoded_pixels(self):
        data = fixture()
        result = video.inspect_mp4_video_presentation(data)
        self.assertEqual(result["master_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(result["presentation_start"], {"num": 0, "den": 1})
        self.assertEqual(result["presentation_end"], {"num": 6, "den": 1})
        self.assertEqual(result["video_sample_count"], 180)
        self.assertFalse(result["samples_decoded_or_reencoded"])
        self.assertFalse(result["sample_content_proven_by_this_clock_check"])
        self.assertFalse(result["native_host_or_provider_attested_here"])

    def test_unsigned_ctts_b_frames_preserve_exact_source_intervals(self):
        # Representative source ordering: offsets 1024,2560,1024,0,512.
        # Per six-frame block the presentation order is 0,3,2,4,1,5.
        offsets = [1024, 2560, 1024, 0, 512, 1024] * 30
        data = fixture(composition_runs=[(1, offset) for offset in offsets])
        result = video.inspect_mp4_video_presentation(data)
        self.assertTrue(result["decode_order_rearranged"])
        self.assertEqual(result["composition_time_version"], 0)
        self.assertEqual(result["initial_edit_media_time"], 1024)
        self.assertTrue(result["all_source_frame_intervals_exact"])

    def test_signed_ctts_b_frames_allow_negative_composition_offsets(self):
        data = fixture(media_time=0, composition_version=1,
                       composition_runs=[(1, offset) for offset in [0, 512, -512] * 60])
        result = video.inspect_mp4_video_presentation(data)
        self.assertTrue(result["decode_order_rearranged"])
        self.assertEqual(result["composition_time_version"], 1)
        self.assertEqual(result["presentation_end"], {"num": 6, "den": 1})

    def test_missing_ctts_is_valid_only_for_unshifted_decode_order(self):
        result = video.inspect_mp4_video_presentation(fixture(media_time=0, composition_runs=[]))
        self.assertIsNone(result["composition_time_version"])
        self.assertFalse(result["decode_order_rearranged"])
        with self.assertRaisesRegex(PresentationClockError, "grid_gap_overlap_or_shift"):
            video.inspect_mp4_video_presentation(fixture(composition_runs=[]))

    def test_one_millisecond_final_video_crop_cannot_pass_matching_audio_duration(self):
        with self.assertRaisesRegex(PresentationClockError, "edit_duration_differs"):
            video.inspect_mp4_video_presentation(fixture(edit_duration=5999))

    def test_one_millisecond_final_video_extension_is_rejected(self):
        with self.assertRaisesRegex(PresentationClockError, "edit_duration_differs"):
            video.inspect_mp4_video_presentation(fixture(edit_duration=6001))

    def test_changed_track_header_duration_is_not_hidden_by_exact_edit(self):
        with self.assertRaisesRegex(PresentationClockError, "track_duration_differs"):
            video.inspect_mp4_video_presentation(fixture(track_duration=5999))

    def test_changed_movie_duration_is_not_hidden_by_exact_video_edit(self):
        with self.assertRaisesRegex(PresentationClockError, "movie_duration_differs"):
            video.inspect_mp4_video_presentation(fixture(movie_duration=5999))

    def test_coherent_container_shortening_still_disagrees_with_source(self):
        with self.assertRaisesRegex(PresentationClockError, "movie_duration_differs"):
            video.inspect_mp4_video_presentation(fixture(movie_duration=5999, track_duration=5999,
                                                       edit_duration=5999))

    def test_wrong_media_duration_is_rejected_before_presentation(self):
        with self.assertRaisesRegex(PresentationClockError, "media_duration_disagrees"):
            video.inspect_mp4_video_presentation(fixture(media_duration=92159))

    def test_composition_offset_gap_is_rejected(self):
        with self.assertRaisesRegex(PresentationClockError, "grid_gap_overlap_or_shift"):
            video.inspect_mp4_video_presentation(fixture(composition_runs=[(179, 1024), (1, 1025)]))

    def test_duplicate_composition_start_is_rejected_as_overlap(self):
        with self.assertRaisesRegex(PresentationClockError, "grid_gap_overlap_or_shift"):
            video.inspect_mp4_video_presentation(fixture(composition_runs=[(179, 1024), (1, 512)]))

    def test_final_frame_duration_is_checked_not_only_its_start(self):
        with self.assertRaisesRegex(PresentationClockError, "frame_duration_differs"):
            video.inspect_mp4_video_presentation(fixture(stts_runs=[(179, 512), (1, 511)],
                                                       media_duration=92159))

    def test_shifted_edit_origin_is_not_validated_from_decode_order(self):
        with self.assertRaisesRegex(PresentationClockError, "grid_gap_overlap_or_shift"):
            video.inspect_mp4_video_presentation(fixture(media_time=1025,
                                                       composition_runs=[(180, 1024)]))

    def test_multiple_empty_and_nonunit_edits_fail_closed(self):
        for edits in ([(3000, 1024, 65536), (3000, 1024, 65536)],
                      [(6000, -1, 65536)], [(6000, 1024, 32768)]):
            with self.subTest(edits=edits), self.assertRaises(PresentationClockError):
                video.inspect_mp4_video_presentation(fixture(edits=edits))

    def test_composition_count_must_match_all_video_samples(self):
        for count in (179, 181):
            with self.subTest(count=count), self.assertRaisesRegex(PresentationClockError, "timing_sample"):
                video.inspect_mp4_video_presentation(fixture(composition_runs=[(count, 1024)]))

    def test_decode_count_must_match_source_frame_inventory(self):
        with self.assertRaisesRegex(PresentationClockError, "timing_sample"):
            video.inspect_mp4_video_presentation(fixture(stts_runs=[(179, 512)]))

    def test_sample_size_inventory_matches_timing_including_variable_sizes(self):
        with self.assertRaisesRegex(PresentationClockError, "sample_size_count"):
            video.inspect_mp4_video_presentation(fixture(stsz_count=179))
        self.assertEqual(video.inspect_mp4_video_presentation(fixture(fixed_size=0))[
            "video_sample_count"], 180)

    def test_single_video_track_is_required(self):
        with self.assertRaisesRegex(PresentationClockError, "track_inventory"):
            video.inspect_mp4_video_presentation(fixture(duplicate_video=True))

    def test_disabled_track_and_non_h264_description_fail_closed(self):
        for change in ({"flags": 0}, {"flags": 1}, {"sample_entry": b"hev1"}):
            with self.subTest(change=change), self.assertRaises(PresentationClockError):
                video.inspect_mp4_video_presentation(fixture(**change))

    def test_header_version_one_retains_exact_edit_and_track_clocks(self):
        result = video.inspect_mp4_video_presentation(fixture(header_version=1))
        self.assertEqual(result["video_media_ticks"], 92160)

    def test_rational_frame_rate_remains_integer_based(self):
        data = fixture(movie_duration=6006, track_duration=6006, edit_duration=6006,
                       timescale=30000, sample_delta=1001, media_time=2002)
        result = video.inspect_mp4_video_presentation(data, fps_num=30000, fps_den=1001)
        self.assertEqual(result["presentation_end"], {"num": 3003, "den": 500})

    def test_unsupported_fragmentation_and_truncation_fail_closed(self):
        for data in (fixture(fragmented=True), fixture() + box(b"moof", b""), fixture()[:-1]):
            with self.subTest(size=len(data)), self.assertRaises(PresentationClockError):
                video.inspect_mp4_video_presentation(data)

    def test_source_counts_and_frame_rate_have_closed_integer_budgets(self):
        for profile in ({"expected_frames": True}, {"expected_frames": video.MAX_VIDEO_FRAMES + 1},
                        {"fps_num": True}, {"fps_num": 0}, {"fps_den": 0}):
            with self.subTest(profile=profile), self.assertRaises(PresentationClockError):
                video.inspect_mp4_video_presentation(fixture(), **profile)

    def test_original_path_wrapper_requires_the_authenticated_container(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.mp4"
            raw = fixture()
            path.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            result = video.inspect_original_mp4_video_presentation(path, sha)
            self.assertEqual(result["master_sha256"], sha)
            self.assertEqual(path.read_bytes(), raw)
            path.write_bytes(fixture(edit_duration=5999))
            with self.assertRaisesRegex(PresentationClockError, "original_hash_changed"):
                video.inspect_original_mp4_video_presentation(path, sha)


class FilmSelectedVideoPresentationTests(unittest.TestCase):
    def test_historical_receipt_without_video_witness_still_recomputes_exact_intervals(self):
        import native_film180_evidence_selection as film
        import native_evidence_projection as core
        raw = fixture()
        receipt = {"master_sha256": hashlib.sha256(raw).hexdigest()}
        actual = film.verify_original_video_presentation(raw, receipt)
        self.assertNotIn("source_bound_video_presentation", receipt)
        self.assertEqual(actual["presentation_start"], {"num": 0, "den": 1})
        self.assertEqual(actual["presentation_end"], {"num": 6, "den": 1})
        self.assertEqual(core.project(actual, film.VIDEO_PRESENTATION, exact=True), actual)

    def test_omitted_video_witness_does_not_admit_authenticated_final_frame_crop(self):
        import native_film180_evidence_selection as film
        import native_evidence_projection as core
        raw = fixture(edit_duration=5999)
        receipt = {"master_sha256": hashlib.sha256(raw).hexdigest()}
        with self.assertRaisesRegex(core.SelectionError, "video_edit_duration_differs_from_source"):
            film.verify_original_video_presentation(raw, receipt)
        self.assertNotIn("source_bound_video_presentation", receipt)


if __name__ == "__main__":
    unittest.main()
