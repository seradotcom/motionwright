"""Synthetic fixtures for the fixed Blender pixel observer, never acceptance.

Real FFmpeg tests decode tiny synthetic PNGs; no Blender render is performed.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_blender_product_pixels as pixels
import native_direct_mediated_parity as parity


def chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))


def png(rgba=b"\x10\x20\x30\xff\x40\x50\x60\xff", *,
        width=2, height=1, note=b"", header=None, extra=b"") -> bytes:
    require_size = width * height * 4
    if len(rgba) != require_size:
        raise ValueError("Synthetic fixture RGBA size mismatch")
    ihdr = header or struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4]
                    for y in range(height))
    metadata = chunk(b"tEXt", b"synthetic-fixture\0" + note) if note else b""
    return (parity.PNG_MAGIC + chunk(b"IHDR", ihdr) + extra
            + chunk(b"IDAT", zlib.compress(rows)) + metadata + chunk(b"IEND", b""))


def synthetic_source_pair():
    """Wire fixture only: no Broker authority or native witness is constructed."""
    ids = {key: f"{i:08x}-0000-4000-8000-000000000000"
           for i, key in enumerate(("project_id", "scene_id", "capsule_id"), 1)}
    expected = {"generation": "00000004-0000-4000-8000-000000000000", "revision": 6}
    after = {**expected, "revision": 10}
    recipe_id = "00000005-0000-4000-8000-000000000000"
    recipe_sha = "a" * 64
    seed = {"schema": "motionwright.blender.product-scene-seed/1", **ids,
            "expected": expected, "source_asset_id": recipe_id, "source_sha256": recipe_sha,
            "product_scene": {"version": 1, "annotation": "ORIGINAL"}}
    original_file = {"relative_path": "synthetic-original.blend", "bytes": 100, "sha256": "b" * 64}
    edited_file = {"relative_path": "synthetic-edited.blend", "bytes": 101, "sha256": "c" * 64}
    request = {**ids, "expected": expected, "request_id": "synthetic-pixel-unit", "annotation_body": "EDITED"}
    def cut(frame, digest):
        path = f"blender-product-frame{frame}.png"
        return {"frame": frame, "expected_camera": "synthetic-Surface",
                "render_ack": {"changed": True, "path": path, "format": "png", "legitimate_extra": 1},
                "scene_after_render": {"frame": frame, "camera": "synthetic-Surface",
                                       "engine": "CYCLES", "resolution": [pixels.WIDTH, pixels.HEIGHT],
                                       "name": "synthetic-Scene"},
                "annotation_hidden_after_render": frame == 600,
                "artifact": {"relative_path": path, "bytes": 100, "sha256": digest,
                             "width": pixels.WIDTH, "height": pixels.HEIGHT,
                             "png_header_checked": True, "decoded_media_verified": False}}
    first, second = cut(601, "d" * 64), cut(601, "e" * 64)
    create = {"schema": "motionwright.blender.product-create-probe-save/1", **ids,
              "expected": expected, "recipe_source_asset_id": recipe_id,
              "recipe_source_sha256": recipe_sha, "output": original_file,
              "cut_probes": [cut(600, "f" * 64), first]}
    edit = {"schema": "motionwright.blender.product-editable-cycle/1", "project_id": ids["project_id"],
            "expected": {**expected, "revision": 8}, "input_source": original_file, "output": edited_file,
            "edit": {"parameter": "product.annotation.body", "before": "ORIGINAL", "after": "EDITED"},
            "one_field_edit_readback_verified": True}
    verify = {"schema": "motionwright.blender.product-editable-cycle/1", "project_id": ids["project_id"],
              "expected": after, "reopened_source": edited_file,
              "annotation": {"original": "ORIGINAL", "persisted": "EDITED"},
              "edited_source_fresh_reopen_performed": True, "surface_probe": second,
              "original_surface_png_sha256": first["artifact"]["sha256"]}
    source = {**ids, "expected": expected, "source_asset_id": recipe_id,
              "source_sha256": recipe_sha, "canonical_scene_duration": {"num": "40", "den": "1"},
              "native_frame_origin": 1, "native_frame_count": 1200, "rate": {"num": 30, "den": 1},
              "internal_cuts": [{"camera": "camera_form", "first_native_frame": 1},
                                {"camera": "camera_surface", "first_native_frame": 601}]}
    value = {"schema": "motionwright.blender.product-cycle-caller/1", **ids,
             "expected_before": expected, "expected_after": after,
             "recipe_asset_id": recipe_id, "recipe_sha256": recipe_sha,
             "original_source": original_file, "edited_source": edited_file,
             "create": create, "edit": edit, "verify": verify,
             "same_capsule_repointed": True, "native_sources_persisted_in_cas": True,
             "decoded_pixel_difference_verified": False, "human_acceptance": False}
    native = {"schema": "motionwright.blender.product-cycle-evidence/1",
              "request": request, "expected_before": expected,
              "journal": {"create": {"source_binding": source}},
              "native_terminal": {"status": "ok", "value": value},
              "phase_contract_verified": True, "decoded_pixel_difference_verified": False}
    return seed, native


class PngTransportTests(unittest.TestCase):
    def test_complete_rgba_png_is_validated_without_decoding(self):
        result = pixels.checked_png(png(), 2, 1)
        self.assertEqual((result["width"], result["height"], result["bit_depth"], result["color_type"]),
                         (2, 1, 8, 6))
        self.assertFalse(result["animated"])

    def test_metadata_crc_corruption_truncation_and_trailing_bytes_are_rejected(self):
        good = png(note=b"one")
        bad_crc = bytearray(good)
        idat = good.index(b"IDAT")
        bad_crc[idat + 4] ^= 1
        for bad in (bytes(bad_crc), good[:-1], good[:20], good + b"trailing", good + good):
            with self.subTest(size=len(bad)):
                with self.assertRaises(Exception):
                    pixels.checked_png(bad, 2, 1)

    def test_dimensions_oversize_depth_and_animation_fail_before_process(self):
        bad_header = struct.pack(">IIBBBBB", 3, 1, 8, 6, 0, 0, 0)
        depth = struct.pack(">IIBBBBB", 2, 1, 16, 6, 0, 0, 0)
        color = struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)
        animation = chunk(b"acTL", struct.pack(">II", 1, 0))
        with mock.patch("native_direct_mediated_parity.decoded_rgba_png_capture") as decoder:
            for bad in (png(header=bad_header), png(header=depth), png(header=color),
                        png(extra=animation), b"x" * (pixels.MAX_PNG + 1)):
                with self.assertRaises(Exception):
                    pixels.checked_png(bad, 2, 1)
            with self.assertRaisesRegex(Exception, "geometry_budget"):
                pixels.checked_png(png(), 3840, 3840)
            decoder.assert_not_called()

    def test_independent_decoder_rejects_bad_header_before_starting(self):
        with mock.patch("manim_native_readback.run_bounded") as decoder:
            for arguments in ((png(), 3, 1), (png(), 5000, 1), (png()[:20], 2, 1)):
                with self.assertRaises(Exception):
                    parity.decoded_rgba_png_capture(
                        *arguments, ffmpeg=Path("/unused"), executable_sha256="a" * 64, capture={})
            decoder.assert_not_called()


class SourceBindingTests(unittest.TestCase):
    def test_caller_join_allows_legitimate_extra_ack_fields_and_keeps_native_flags(self):
        seed, native = synthetic_source_pair()
        before = copy.deepcopy(native)
        original, edited, binding = pixels.bound_surface_pair(seed, native)
        self.assertEqual(original["sha256"], "d" * 64)
        self.assertEqual(edited["sha256"], "e" * 64)
        self.assertEqual(binding["native_frame"], 601)
        self.assertFalse(binding["geometry_or_camera_recertified_by_pixel_reader"])
        self.assertEqual(native, before)

    def test_wrong_artifact_source_camera_frame_or_edit_does_not_join(self):
        edits = [
            lambda s, n: n["native_terminal"]["value"]["verify"].update(original_surface_png_sha256="f" * 64),
            lambda s, n: n["native_terminal"]["value"]["verify"]["surface_probe"].update(expected_camera="other"),
            lambda s, n: n["native_terminal"]["value"]["verify"]["surface_probe"].update(frame=600),
            lambda s, n: n["native_terminal"]["value"]["create"].update(recipe_source_sha256="f" * 64),
            lambda s, n: n["native_terminal"]["value"]["edit"]["edit"].update(after="NOT_THE_EDIT"),
            lambda s, n: n["native_terminal"]["value"]["verify"]["surface_probe"]["render_ack"].update(changed=1),
            lambda s, n: n["native_terminal"]["value"]["verify"]["surface_probe"]["artifact"].update(bytes=True),
        ]
        for mutate in edits:
            seed, native = synthetic_source_pair()
            mutate(seed, native)
            with self.assertRaises(Exception):
                pixels.bound_surface_pair(seed, native)

    def test_duration_and_rate_keep_their_distinct_real_wire_types(self):
        for key, value in (
            ("canonical_scene_duration", {"num": 40, "den": 1}),
            ("rate", {"num": "30", "den": "1"}),
            ("native_frame_origin", True),
            ("internal_cuts", [{"camera": "camera_form", "first_native_frame": True},
                               {"camera": "camera_surface", "first_native_frame": 601}]),
        ):
            seed, native = synthetic_source_pair()
            native["journal"]["create"]["source_binding"][key] = value
            with self.assertRaises(Exception):
                pixels.bound_surface_pair(seed, native)

    def test_actual_png_digest_mismatch_stops_before_decoder(self):
        seed, native = synthetic_source_pair()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            create, verify, evidence = (root / name for name in ("create", "verify", "evidence"))
            for path in (create, verify, evidence):
                path.mkdir(mode=0o700)
            (create / pixels.SURFACE_PNG).write_bytes(png())
            record = {}
            # This unit fixture exercises real file bytes and their digest.
            # POSIX directory ownership admission has its own native boundary;
            # Windows has no geteuid, so this test does not certify that boundary.
            with mock.patch.object(pixels, "_plain_owned_directory") as admission, \
                    mock.patch.object(pixels, "retained_decode") as decoder:
                with self.assertRaisesRegex(Exception, "artifact_bytes_changed"):
                    pixels.observe_product_surface_pixels(
                        seed, native, create_root=create, verify_root=verify, evidence_root=evidence,
                        native_evidence_sha256="a" * 64, ffmpeg=Path("/unused"),
                        executable_sha256="b" * 64, record=record)
                self.assertEqual(admission.call_args_list,
                                 [mock.call(create), mock.call(verify), mock.call(evidence)])
                decoder.assert_not_called()
            self.assertFalse(record["decoded_pixel_difference_verified"])


class PixelAndCustodyTests(unittest.TestCase):
    def test_exact_delta_and_opacity_are_narrow_claims(self):
        original = b"\x10\x20\x30\xff\x40\x50\x60\xff"
        edited = b"\x11\x20\x30\xff\x40\x50\x60\xff"
        result = pixels.rgba_difference(original, edited, 2, 1)
        self.assertTrue(result["decoded_pixel_difference_verified"])
        self.assertEqual(result["changed_rgb_pixel_count"], 1)
        self.assertEqual(result["rgb_absolute_error_sum"], 1)
        self.assertEqual(result["maximum_rgb_channel_difference"], 1)
        for key in ("constant_background_verified", "exclusive_edit_attribution_verified",
                    "glyph_legibility_verified", "full_fidelity_verified", "human_acceptance"):
            self.assertFalse(result[key])
        with self.assertRaisesRegex(Exception, "must_be_opaque"):
            pixels.rgba_difference(original, edited[:-1] + b"\x80", 2, 1)
        with self.assertRaisesRegex(Exception, "decoded_size"):
            pixels.rgba_difference(original, edited[:-1], 2, 1)

    def test_original_decoder_failure_survives_both_stream_custody_failures(self):
        original = RuntimeError("synthetic original decoder failure")
        def fail(_png, _width, _height, *, capture, **_kwargs):
            capture.update({"state": "TERMINAL_OBSERVED", "stdout": b"original partial bytes",
                            "stderr": b"original stderr", "terminal": {"exit_code": 7}})
            raise original
        record = {}
        with mock.patch.object(pixels, "decoded_rgba_png_capture", side_effect=fail):
            with mock.patch.object(pixels, "new_bytes", side_effect=OSError("synthetic custody failure")):
                with self.assertRaises(RuntimeError) as observed:
                    pixels.retained_decode(b"synthetic input", ffmpeg=Path("/unused"),
                                           executable_sha256="a" * 64,
                                           prefix=Path("/unused/original"), record=record)
        self.assertIs(observed.exception, original)
        self.assertEqual(record["terminal"]["exit_code"], 7)
        self.assertEqual(record["streams"]["stdout"]["sha256"],
                         hashlib.sha256(b"original partial bytes").hexdigest())
        self.assertEqual(len(record["stream_custody_errors"]), 2)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg required for synthetic PNG decoder integration")
class RealSyntheticDecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = Path(shutil.which("ffmpeg")).resolve(strict=True)
        cls.sha256 = parity.sha256_path(cls.ffmpeg)

    def decode(self, body):
        pixels.checked_png(body, 2, 1)
        captured = {}
        raw = parity.decoded_rgba_png_capture(body, 2, 1, ffmpeg=self.ffmpeg,
                                             executable_sha256=self.sha256, capture=captured)
        self.assertTrue(captured["decoder"]["binary_identity_checked_after"])
        self.assertEqual(captured["terminal"]["exit_code"], 0)
        return raw

    def test_metadata_only_png_changes_do_not_establish_a_pixel_edit(self):
        first, second = png(note=b"one"), png(note=b"two")
        self.assertNotEqual(hashlib.sha256(first).digest(), hashlib.sha256(second).digest())
        original, edited = self.decode(first), self.decode(second)
        result = pixels.rgba_difference(original, edited, 2, 1)
        self.assertEqual(result["original_rgba_sha256"], result["edited_rgba_sha256"])
        self.assertEqual(result["changed_rgb_pixel_count"], 0)
        self.assertFalse(result["decoded_pixel_difference_verified"])

    def test_real_decoder_preserves_one_changed_synthetic_rgb_sample(self):
        original = self.decode(png())
        edited = self.decode(png(b"\x11\x20\x30\xff\x40\x50\x60\xff"))
        result = pixels.rgba_difference(original, edited, 2, 1)
        self.assertTrue(result["decoded_pixel_difference_verified"])
        self.assertEqual(result["changed_rgb_pixel_count"], 1)
        self.assertEqual(result["maximum_rgb_channel_difference"], 1)

    def test_real_decoder_transparency_does_not_pass_opaque_profile(self):
        original = self.decode(png())
        edited = self.decode(png(b"\x10\x20\x30\x80\x40\x50\x60\xff"))
        with self.assertRaisesRegex(Exception, "must_be_opaque"):
            pixels.rgba_difference(original, edited, 2, 1)


if __name__ == "__main__":
    unittest.main()
