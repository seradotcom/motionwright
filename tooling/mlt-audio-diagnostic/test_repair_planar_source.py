"""Source-locked negative and content controls for MLT 7.22 planar AAC repair.

Requires MOTIONWRIGHT_MLT_V722_ORIGINAL pointing at a separately authenticated
upstream v7.22.0 source to run the original-source positive controls. The
standalone C/FFmpeg test is a separate, explicit native compiler gate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "source_locked_mlt_repair", HERE / "repair_planar_source.py"
)
assert SPEC is not None and SPEC.loader is not None
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


def original_path() -> Path | None:
    value = os.environ.get("MOTIONWRIGHT_MLT_V722_ORIGINAL")
    return Path(value) if value else None


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoder_submission(source: bytes) -> bytes:
    """Extract the authenticated actual callsite for the separate C gate."""
    if digest(source) not in {
        "246e84b02d25d0fe5ea08d59d5abb03c6dfd42d5d15dabeb464c8051f8ed8de0",
        "fd5095ba7a676733b4b0642e92b931b986ee5fbfb23c800066d8996e4f428121",
    }:
        raise ValueError("encoder callsite requires an exact authenticated consumer")
    text = source.decode("utf-8")
    start = text.index(repair.OPTIMIZED_START)
    stop = text.index("} else {", start)
    scoped = text[start:stop]
    first = "            void *p = ctx->audio_buf_1;\n"
    last = "            int ret = avcodec_send_frame(codec, samples ? ctx->audio_avframe : NULL);\n"
    if scoped.count(first) != 1 or scoped.count(last) != 1:
        raise ValueError("encoder callsite boundary is not unique")
    begin = scoped.index(first)
    end = scoped.index(last) + len(last)
    if begin >= end:
        raise ValueError("encoder callsite boundaries are reversed")
    return scoped[begin:end].encode("utf-8")


def synthetic_tail(samples: int = 576, prepared: int = 1024) -> tuple[bytes, bytes]:
    """Model solely the source's compact-vs-declared-stride channel placement.

    Explicitly not a substitute for avcodec_fill_audio_frame with libavutil.
    """
    if not 0 <= samples <= prepared:
        raise ValueError("partial frame is invalid")
    input_interleaved = b"".join(
        struct.pack("<ff", 0.125 + index / 4096, 0.625 - index / 4096)
        for index in range(samples)
    )
    compact = bytearray(prepared * 2 * 4)
    repaired = bytearray(prepared * 2 * 4)
    for index in range(samples):
        left = input_interleaved[index * 8:index * 8 + 4]
        right = input_interleaved[index * 8 + 4:index * 8 + 8]
        compact[index * 4:index * 4 + 4] = left
        compact[(samples + index) * 4:(samples + index + 1) * 4] = right
        repaired[index * 4:index * 4 + 4] = left
        repaired[(prepared + index) * 4:(prepared + index + 1) * 4] = right
    return bytes(compact), bytes(repaired)


class OriginalPlanarRepairTests(unittest.TestCase):
    def require_original(self) -> Path:
        src = original_path()
        if src is None:
            self.skipTest("authentic upstream MLT 7.22.0 source not supplied")
        return src

    def test_pinned_upstream_source_changes_only_a_new_copy(self):
        original = self.require_original()
        original_bytes = repair.checked_original(original)
        self.assertEqual(len(original_bytes), repair.ORIGINAL_BYTES)
        self.assertEqual(digest(original_bytes), repair.ORIGINAL_SHA256)
        transformed, helper = repair.repair_exact_original(original_bytes)
        self.assertNotEqual(transformed, original_bytes)
        self.assertEqual(len(transformed), 99266)
        self.assertEqual(
            digest(transformed),
            "fd5095ba7a676733b4b0642e92b931b986ee5fbfb23c800066d8996e4f428121",
        )
        self.assertEqual(digest(helper), "d90db74a261da8053a7f8258f1b9975e692ab54888408cd0a2818ee26e720bf9")
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_repair_retains_four_original_formats_frame_clock_and_send(self):
        transformed, helper = repair.repair_exact_original(
            repair.checked_original(self.require_original())
        )
        text = transformed.decode("utf-8")
        self.assertIn("av_samples_get_buffer_size(&linesize,", text)
        self.assertIn("av_get_bytes_per_sample(sample_fmt)", text)
        self.assertIn("av_sample_fmt_is_planar(sample_fmt)", text)
        self.assertIn("FFMAX(samples, ctx->audio_input_frame_size)", text)
        self.assertEqual(text.count("p = interleaved_to_planar(samples,"), 4)
        for dtype in repair.SAMPLE_TYPES:
            self.assertIn(f"ctx->channels, p, sizeof({dtype}),", text)
        self.assertEqual(text.count("avcodec_send_frame(codec, samples ? ctx->audio_avframe : NULL);"), 2)
        self.assertIn("avcodec_fill_audio_frame(ctx->audio_avframe,", text)
        self.assertIn("if (!p) {", text)
        self.assertEqual(transformed.count(helper), 1)

    def test_last_frame_callsite_preserves_stride_and_valid_clock_order(self):
        source = repair.checked_original(self.require_original())
        transformed, helper = repair.repair_exact_original(source)
        callsite = encoder_submission(transformed).decode("utf-8")
        self.assertEqual(digest(helper), "d90db74a261da8053a7f8258f1b9975e692ab54888408cd0a2818ee26e720bf9")
        self.assertIn("samples > 0 && samples < ctx->audio_avframe->nb_samples", callsite)
        self.assertIn("codec->codec->capabilities & AV_CODEC_CAP_SMALL_LAST_FRAME", callsite)
        positions = [callsite.index(token) for token in (
            repair.PREPARED_ASSIGNMENT,
            "ctx->audio_avframe->pts = ctx->sample_count[i];",
            "avcodec_fill_audio_frame(",
            "ctx->audio_avframe->nb_samples = samples;",
            "ctx->sample_count[i] += ctx->audio_avframe->nb_samples;",
            "avcodec_send_frame(codec, samples ? ctx->audio_avframe : NULL);",
        )]
        self.assertEqual(positions, sorted(positions))
        # The mapped branch and termination/NULL-drain loops remain authentic.
        def unchanged_suffix(raw):
            text = raw.decode("utf-8")
            begin = text.index(repair.OPTIMIZED_START)
            return text[text.index("} else {", begin):]
        self.assertEqual(unchanged_suffix(source), unchanged_suffix(transformed))

    def test_last_frame_callsite_refuses_unbound_source(self):
        source = repair.checked_original(self.require_original())
        transformed, _ = repair.repair_exact_original(source)
        for foreign in (b"", source, transformed + b"\n", transformed[:-1]):
            with self.subTest(size=len(foreign)):
                with self.assertRaisesRegex(ValueError, "exact authenticated consumer"):
                    encoder_submission(foreign)

    def test_unsigned_u8p_silence_is_nonzero_and_legacy_zero_is_rejected(self):
        """Independent byte-level guard; libavutil is checked by the C lane."""
        original = repair.checked_original(self.require_original())
        transformed, helper = repair.repair_exact_original(original)
        required = (
            b"memset(buffer, sample_fmt == AV_SAMPLE_FMT_U8P ? 0x80 : 0,\n"
            b"           AUDIO_ENCODE_BUFFER_SIZE);"
        )
        self.assertIn(required, helper)
        self.assertNotIn(b"memset(buffer, 0, AUDIO_ENCODE_BUFFER_SIZE);", helper)
        # U8/U8P represents signed-equivalent zero as 128. An all-zero
        # audio tail means full-scale negative DC, not silence.
        self.assertEqual(bytes([0x80]) * 16, b"\x80" * 16)
        self.assertNotEqual(bytes(16), bytes([0x80]) * 16)
        old_helper = helper.replace(
            required, b"memset(buffer, 0, AUDIO_ENCODE_BUFFER_SIZE);",
        )
        self.assertNotEqual(old_helper, helper)
        self.assertEqual(old_helper.count(b"memset(buffer, 0, AUDIO_ENCODE_BUFFER_SIZE);"), 1)
        self.assertEqual(transformed.count(helper), 1)
        self.assertEqual(transformed.count(old_helper), 0)

    def test_source_repair_fixes_silence_without_reordering_codec_paths(self):
        transformed, helper = repair.repair_exact_original(
            repair.checked_original(self.require_original())
        )
        self.assertIn(b"sample_fmt == AV_SAMPLE_FMT_U8P ? 0x80 : 0", helper)
        self.assertIn(b"av_samples_get_buffer_size(&linesize,", helper)
        # The only type-specific change is the silence fill; never remove
        # unsigned samples from the four original encoder branches.
        self.assertEqual(transformed.count(b"codec->sample_fmt == AV_SAMPLE_FMT_U8P"), 1)
        self.assertEqual(transformed.count(b"codec->sample_fmt == AV_SAMPLE_FMT_FLTP"), 1)
        self.assertEqual(transformed.count(b"codec->sample_fmt == AV_SAMPLE_FMT_S16P"), 1)
        self.assertEqual(transformed.count(b"codec->sample_fmt == AV_SAMPLE_FMT_S32P"), 1)

    def test_output_readback_and_repeated_generation_are_fail_closed(self):
        source = self.require_original()
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch)
            result = repair.generate(source, output)
            self.assertEqual(result["original_sha256"], repair.ORIGINAL_SHA256)
            self.assertFalse(result["provider_compiled"])
            self.assertFalse(result["master_audio_tail_corrected"])
            self.assertFalse(result["product_pin_modified"])
            receipt = json.loads((output / "repair-receipt.json").read_text())
            self.assertEqual(receipt, result)
            self.assertEqual(digest((output / "consumer_avformat.c").read_bytes()), receipt["repaired_source_sha256"])
            self.assertEqual(digest((output / "planar-helper-under-test.h").read_bytes()), receipt["exact_test_helper_sha256"])
            with self.assertRaises(FileExistsError):
                repair.generate(source, output)
            self.assertEqual(json.loads((output / "repair-receipt.json").read_text()), receipt)

    def test_reject_previously_instrumented_or_modified_original(self):
        source = self.require_original()
        original = repair.checked_original(source)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "modified_original.c"
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            with self.assertRaisesRegex(ValueError, "source content or generation"):
                repair.checked_original(path)
            repaired, _ = repair.repair_exact_original(original)
            with self.assertRaisesRegex(ValueError, "nonoriginal"):
                repair.repair_exact_original(repaired)

    def test_missing_wrong_size_and_non_absolute_input_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "fake.c"
            path.write_bytes(b"a" * 4096)
            with self.assertRaisesRegex(ValueError, "size"):
                repair.checked_original(path)
            with self.assertRaisesRegex(ValueError, "absolute"):
                repair.checked_original(Path("relative_original.c"))

    def test_missing_output_directory_rejected_without_writing(self):
        source = self.require_original()
        with tempfile.TemporaryDirectory() as scratch:
            missing = Path(scratch) / "not_created"
            with self.assertRaisesRegex(ValueError, "fresh existing"):
                repair.generate(source, missing)
            self.assertFalse(missing.exists())

    def test_source_roundtrip_never_invents_a_producer_pass(self):
        source = self.require_original()
        with tempfile.TemporaryDirectory() as scratch:
            plan = repair.generate(source, Path(scratch))
            for claim in ("provider_compiled", "provider_dispatched",
                          "master_audio_tail_corrected", "native_master_accepted"):
                self.assertIs(plan[claim], False, claim)


class PlanarTailModelNegativeControls(unittest.TestCase):
    def test_partial_576_of_1024_compact_second_plane_is_misaligned(self):
        compact, repaired = synthetic_tail(576, 1024)
        self.assertEqual(len(compact), 8192)
        expected_first_right = struct.pack("<f", 0.625)
        self.assertNotEqual(compact[4096:4100], expected_first_right)
        self.assertEqual(repaired[4096:4100], expected_first_right)
        self.assertEqual(repaired[576 * 4:576 * 4 + 4], bytes(4))
        self.assertEqual(repaired[-4:], bytes(4))

    def test_complete_frame_preserves_original_byte_placement(self):
        old, corrected = synthetic_tail(1024, 1024)
        self.assertEqual(old, corrected)

    def test_very_short_tail_remains_two_distinct_channels(self):
        old, corrected = synthetic_tail(1, 1024)
        self.assertNotEqual(old, corrected)
        self.assertEqual(corrected[:4], struct.pack("<f", 0.125))
        self.assertEqual(corrected[4096:4100], struct.pack("<f", 0.625))

    def test_invalid_tail_source_budget_is_rejected(self):
        with self.assertRaises(ValueError):
            synthetic_tail(1025, 1024)
        with self.assertRaises(ValueError):
            synthetic_tail(-1, 1024)


if __name__ == "__main__":
    unittest.main()
