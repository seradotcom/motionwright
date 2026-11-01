"""AAC edit-clock regressions; constructed tables are not native media proof."""
from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_mp4_aac_presentation as clock
import native_film180_media_readback as film


def box(kind, body, *, extended=False):
    if extended:
        return struct.pack(">I4sQ", 1, kind, len(body) + 16) + body
    return struct.pack(">I4s", len(body) + 8, kind) + body


def full(kind, body, *, version=0):
    return box(kind, bytes([version, 0, 0, 0]) + body)


def header(kind, timescale, duration, *, version=0):
    if version == 1:
        body = struct.pack(">QQIQ", 0, 0, timescale, duration)
    else:
        body = struct.pack(">IIII", 0, 0, timescale, duration)
    return full(kind, body + bytes(4), version=version)


def fixture(*, priming=1024, segment=6000, rate=0x10000,
            movie_scale=1000, movie_duration=6000, audio_scale=48000,
            media_duration=289024, counts=((282, 1024), (1, 256)),
            packet_count=283, channels=2, entry_rate=48000,
            duplicate=False, extra_audio=False, fragmented=False,
            composition=False, version=0, extended=False, edits=1,
            fixed_packet_size=8):
    sample = bytes(6) + struct.pack(">H", 1) + bytes(8)
    sample += struct.pack(">HHHHI", channels, 16, 0, 0, entry_rate << 16)
    table = full(b"stsd", struct.pack(">I", 1) + box(b"mp4a", sample))
    timing = full(b"stts", struct.pack(">I", len(counts)) + b"".join(
        struct.pack(">II", n, delta) for n, delta in counts))
    table += timing * (2 if duplicate else 1)
    sizes = struct.pack(">II", fixed_packet_size, packet_count)
    if not fixed_packet_size:
        sizes += struct.pack(">I", 8) * packet_count
    table += full(b"stsz", sizes)
    if composition:
        table += full(b"ctts", struct.pack(">III", 1, packet_count, 1))
    media = header(b"mdhd", audio_scale, media_duration, version=version)
    media += full(b"hdlr", bytes(4) + b"soun" + bytes(12))
    media += box(b"minf", box(b"stbl", table))
    entry = struct.pack(">QqI" if version else ">IiI", segment, priming, rate)
    edit = full(b"elst", struct.pack(">I", edits) + entry * edits, version=version)
    track = box(b"trak", box(b"edts", edit) + box(b"mdia", media))
    movie = header(b"mvhd", movie_scale, movie_duration, version=version)
    movie += track * (2 if extra_audio else 1)
    if fragmented:
        movie += box(b"mvex", b"")
    return box(b"ftyp", b"isom" + bytes(4) + b"isom") + box(b"moov", movie, extended=extended)


def inspect(data=None, **kwargs):
    return clock.inspect_mp4_aac_presentation(
        fixture() if data is None else data,
        source_frames=kwargs.pop("source_frames", 288000),
        decoded_frames=kwargs.pop("decoded_frames", 288768), **kwargs,
    )


class AacPresentationClockTests(unittest.TestCase):
    def test_original_six_second_geometry_explains_768_samples_without_trimming(self):
        data = fixture()
        result = inspect(data)
        self.assertEqual(result["presented_sample_frames"], 288000)
        self.assertEqual(result["initial_edit_skip_samples"], 1024)
        self.assertEqual(result["observed_decoded_samples_after_presentation"], 768)
        self.assertEqual(result["final_packet_duration_samples"], 256)
        self.assertEqual(result["master_sha256"], hashlib.sha256(data).hexdigest())
        self.assertFalse(result["samples_trimmed_or_reencoded"])
        self.assertFalse(result["sample_content_proven_by_this_clock_check"])

    def test_decoder_that_already_honors_final_presentation_is_explicitly_supported(self):
        self.assertEqual(inspect(decoded_frames=288000)[
            "observed_decoded_samples_after_presentation"], 0)

    def test_shifted_priming_does_not_pass_even_with_same_pcm_length(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "media_interval"):
            inspect(fixture(priming=1025))

    def test_changed_final_packet_duration_cannot_be_explained_as_generic_padding(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "duration_disagrees"):
            inspect(fixture(counts=((282, 1024), (1, 257))))

    def test_coherently_changed_packet_and_media_clock_still_rejects_source_mismatch(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "media_interval"):
            inspect(fixture(counts=((282, 1024), (1, 257)), media_duration=289025))

    def test_wrong_edit_and_movie_duration_do_not_override_authored_source(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "presentation_differs"):
            inspect(fixture(segment=6001, movie_duration=6001))

    def test_movie_and_audio_must_share_source_end(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "movie_duration"):
            inspect(fixture(movie_duration=6001))

    def test_arbitrary_decoded_lengths_are_not_accepted_under_a_1024_sample_tolerance(self):
        for count in (287999, 288001, 288767, 288769, 289024):
            with self.subTest(count=count), self.assertRaisesRegex(
                    clock.PresentationClockError, "not_explained_by_container"):
                inspect(decoded_frames=count)

    def test_historical_short33_duration_is_not_accepted_by_trimming_to_input(self):
        bad = fixture(segment=1109, movie_duration=1109, media_duration=54256,
                      counts=((52, 1024), (1, 1008)), packet_count=53)
        with self.assertRaisesRegex(clock.PresentationClockError, "presentation_differs"):
            inspect(bad, source_frames=52800, decoded_frames=53248)

    def test_same_contract_supports_correct_short33_without_hardcoding_film180_duration(self):
        data = fixture(segment=1100, movie_duration=1100, media_duration=53824,
                       counts=((52, 1024), (1, 576)), packet_count=53)
        result = inspect(data, source_frames=52800, decoded_frames=53248)
        self.assertEqual(result["unpresented_final_access_unit_samples"], 448)

    def test_rate_changes_and_empty_edits_are_rejected(self):
        for change, error in (({"rate": 0x8000}, "rate_not_unit"),
                              ({"priming": -1}, "empty_or_unbounded"),
                              ({"priming": 4096}, "empty_or_unbounded"),
                              ({"edits": 2}, "edit_inventory")):
            with self.subTest(change=change), self.assertRaisesRegex(
                    clock.PresentationClockError, error):
                inspect(fixture(**change))

    def test_wrong_channels_sample_rate_or_media_clock_rejected_before_acceptance(self):
        for change in ({"channels": 1}, {"entry_rate": 44100}, {"audio_scale": 44100}):
            with self.subTest(change=change), self.assertRaises(clock.PresentationClockError):
                inspect(fixture(**change))

    def test_duplicate_timing_and_second_audio_track_cannot_replace_originals(self):
        for change in ({"duplicate": True}, {"extra_audio": True}):
            with self.subTest(change=change), self.assertRaises(clock.PresentationClockError):
                inspect(fixture(**change))

    def test_fragmented_or_reordered_audio_is_explicitly_unsupported(self):
        for change in ({"fragmented": True}, {"composition": True}):
            with self.subTest(change=change), self.assertRaises(clock.PresentationClockError):
                inspect(fixture(**change))
        with self.assertRaisesRegex(clock.PresentationClockError, "fragmented"):
            inspect(fixture() + box(b"moof", b""))

    def test_partial_access_unit_must_be_unique_and_terminal(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "not_terminal"):
            inspect(fixture(counts=((1, 256), (282, 1024))))

    def test_packet_count_and_sizes_must_match_timing(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "packet_count_mismatch"):
            inspect(fixture(packet_count=282))
        self.assertEqual(inspect(fixture(fixed_packet_size=0))["aac_packet_count"], 283)

    def test_v1_edit_and_large_box_headers_preserve_exact_integer_clock(self):
        result = inspect(fixture(version=1, extended=True))
        self.assertEqual(result["presented_sample_frames"], 288000)

    def test_truncation_and_box_length_escape_fail_closed(self):
        data = fixture()
        for malformed in (data[:-1], data + b"bad", struct.pack(">I4s", 7, b"moov"),
                          struct.pack(">I4sQ", 1, b"moov", 2**63)):
            with self.subTest(length=len(malformed)), self.assertRaises(clock.PresentationClockError):
                inspect(malformed)

    def test_unknown_full_box_version_rejected(self):
        data = bytearray(fixture())
        data[data.index(b"elst") + 4] = 2
        with self.assertRaisesRegex(clock.PresentationClockError, "unsupported_full_box"):
            inspect(bytes(data))

    def test_zero_timescale_and_invalid_input_types_rejected(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "invalid_clock"):
            inspect(fixture(movie_scale=0))
        for field, value in (("source_frames", True), ("decoded_frames", True),
                             ("sample_rate", 44100)):
            with self.subTest(field=field), self.assertRaises(clock.PresentationClockError):
                inspect(**{field: value})

    def test_box_count_has_explicit_budget(self):
        with self.assertRaisesRegex(clock.PresentationClockError, "box_budget"):
            inspect(box(b"free", b"") * (clock.MAX_BOXES + 1))


class FilmOriginalPresentationTests(unittest.TestCase):
    def test_film_reader_authenticates_container_and_keeps_full_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "master.mp4"
            data = fixture()
            media.write_bytes(data)
            bound = hashlib.sha256(data).hexdigest()
            result = film.inspect_original_audio_presentation(media, bound, 288768)
            self.assertEqual(result["decoded_sample_frames"], 288768)
            self.assertEqual(media.read_bytes(), data)
            media.write_bytes(fixture(priming=1025))
            with self.assertRaisesRegex(film.OriginalMediaMismatch, "sha256_changed"):
                film.inspect_original_audio_presentation(media, bound, 288768)

    def test_film_reader_cannot_bypass_clock_check_with_fresh_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "master.mp4"
            data = fixture(priming=1025)
            media.write_bytes(data)
            with self.assertRaisesRegex(film.OriginalMediaMismatch, "media_interval"):
                film.inspect_original_audio_presentation(media, hashlib.sha256(data).hexdigest(), 288768)

    def test_readback_does_not_reach_content_acceptance_when_presentation_fails(self):
        # Control flow regression only. No mocked result is physical evidence.
        data = fixture(priming=1025)
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "master.mp4"
            media.write_bytes(data)
            bound = hashlib.sha256(data).hexdigest()
            with mock.patch.object(film, "load_json", return_value={}), \
                    mock.patch.object(film, "source_identity", return_value={}), \
                    mock.patch.object(film, "ffprobe_media", return_value={}), \
                    mock.patch.object(film, "decode_selected_video", return_value={}), \
                    mock.patch.object(film, "inspect_selected_pixels", return_value={}), \
                    mock.patch.object(film, "decode_original_audio", return_value=bytes(288768 * 4)), \
                    mock.patch.object(film, "inspect_source_pcm16_stereo_content") as content:
                with self.assertRaisesRegex(film.OriginalMediaMismatch, "media_interval"):
                    film.verified_source_readback(media, bound, media, bound, media, bound,
                                                  media, bound, media, bound)
                content.assert_not_called()


class FilmSelectedPresentationTests(unittest.TestCase):
    def test_closed_projection_preserves_the_whole_byte_bound_clock_witness(self):
        import native_evidence_projection as projection
        import native_film180_evidence_selection as selected
        actual=inspect()
        rule=selected.DECODED.fields["source_bound_audio_presentation"].rule
        self.assertEqual(projection.project(actual,rule,exact=True),actual)
        with self.assertRaises(projection.SelectionError):
            projection.project(dict(actual,unexpected_native_approval=True),rule,exact=True)

    def test_historical_receipt_still_requires_recomputation_from_original_container(self):
        import native_film180_evidence_selection as selected
        data=fixture();receipt={"master_sha256":hashlib.sha256(data).hexdigest()}
        actual=selected.verify_original_audio_presentation(data,bytes(288768*4),receipt)
        self.assertEqual(actual["presentation_end_sample"],288000)

    def test_omitting_the_clock_witness_does_not_admit_a_wrong_presentation(self):
        import native_evidence_projection as projection
        import native_film180_evidence_selection as selected
        data=fixture(segment=6001,movie_duration=6001)
        receipt={"master_sha256":hashlib.sha256(data).hexdigest()}
        with self.assertRaisesRegex(projection.SelectionError,"presentation_differs_from_source"):
            selected.verify_original_audio_presentation(data,bytes(288768*4),receipt)

    def test_selected_witness_cannot_describe_a_different_decoder_length(self):
        import native_evidence_projection as projection
        import native_film180_evidence_selection as selected
        data=fixture();receipt={"master_sha256":hashlib.sha256(data).hexdigest(),
            "source_bound_audio_presentation":inspect(decoded_frames=288000)}
        with self.assertRaisesRegex(projection.SelectionError,"witness-differs"):
            selected.verify_original_audio_presentation(data,bytes(288768*4),receipt)

    def test_selected_container_cannot_borrow_the_receipt_of_another_master(self):
        import native_evidence_projection as projection
        import native_film180_evidence_selection as selected
        with self.assertRaisesRegex(projection.SelectionError,"presentation-master-link"):
            selected.verify_original_audio_presentation(fixture(),bytes(288768*4),{"master_sha256":"0"*64})


if __name__ == "__main__":
    unittest.main()
