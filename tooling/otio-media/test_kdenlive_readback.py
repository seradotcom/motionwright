"""Kdenlive native MLT XML readback and source-integrity unit tests.

Fixtures contain synthetic MLT XML and source media bytes. These tests do
not launch Kdenlive or provide native editor process evidence. Actual
Kdenlive import/save/reopen/export requires a separate authorized app run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kdenlive_readback import (
    KdenliveReadbackRejected, compare_two_native_custody_snapshots,
    inspect_saved_kdenlive, safe_mlt_xml,
)

SHA = lambda data: hashlib.sha256(data).hexdigest()
MEDIA_BYTES = b"FIRST_PARTY_SYNTHETIC_NOT_REAL_MP4" * 64
MEDIA_SHA = SHA(MEDIA_BYTES)


def capsule(root: Path, *, missing: bool = False) -> tuple[Path, str]:
    bundle = root / "capsule"
    (bundle / "media").mkdir(parents=True)
    (bundle / "media" / f"{MEDIA_SHA}.mp4").write_bytes(MEDIA_BYTES)
    row = {
        "scene_id": "scene-original-1",
        "source_sha256": MEDIA_SHA, "path": f"media/{MEDIA_SHA}.mp4",
        "source_start_frame": 0, "duration_frames": 30, "source_fps": 30,
    }
    manifest = {
        "schema": "motionwright.otio-portable-media-bridge/1",
        "output_profile": "portable-media-cut-v2",
        "nle_import_verified": False,
        "publishing_authorized": False,
        "bound_media": [row],
        "missing_scene_ids": ["absent-authorized-video"] if missing else [],
        "scene_order": ["scene-original-1"],
    }
    raw = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
    (bundle / "bridge-manifest.json").write_bytes(raw)
    return bundle, SHA(raw)


def mlt_xml(*, media_name: str, rate: int = 30, missing_bin: bool = False,
            missing_tractor: bool = False, missing_timeline: bool = False,
            duplicate_ids: bool = False, foreign_media: str | None = None,
            namespace_error: bool = False) -> bytes:
    source = foreign_media or media_name
    extra = ' kdenlive:compat="1"' if namespace_error else ""
    return (
        f'<mlt producer="main_bin"{extra} version="7.22.0">'
        f'<profile width="320" height="180" frame_rate_num="{rate}" '
        f'frame_rate_den="1"/>'
        f'<chain id="chain1"><property name="resource">{source}</property></chain>'
        f'<playlist id="{"chain1" if duplicate_ids else "timeline0"}">'
        + ('' if missing_timeline else '<entry producer="chain1" in="0" out="29"/>')
        + '</playlist>'
        + ('' if missing_bin else '<playlist id="main_bin">'
           '<entry producer="chain1" in="0" out="29"/></playlist>')
        + ('' if missing_tractor else
           '<tractor id="final_tractor"><property name="kdenlive:projectTractor">'
           '1</property><track producer="timeline0"/></tractor>')
        + '</mlt>'
    ).encode("utf-8")


def native_file(root: Path, raw: bytes, name: str = "native.kdenlive") -> tuple[Path, str]:
    p = root / name
    p.write_bytes(raw)
    return p, SHA(raw)


class KdenliveNativeReadbackTests(unittest.TestCase):
    def scene(self, temp: Path, *, missing: bool = False, **kwargs):
        bundle, manifest_sha = capsule(temp, missing=missing)
        original, project_sha = native_file(
            bundle, mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4", **kwargs),
        )
        return bundle, manifest_sha, original, project_sha

    def test_native_xml_saved_with_bound_original_media_and_source_identity(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            result = inspect_saved_kdenlive(project, sha, bundle, seal)
            self.assertTrue(result["native_kdenlive_main_bin_and_tractor"])
            self.assertTrue(result["exact_original_source_SHA_set_conserved"])
            self.assertEqual(result["observed_source_media"][0]["original_media_sha256"],
                             MEDIA_SHA)
            self.assertEqual(result["native_saved_frame_rate"], "30/1")
            self.assertFalse(result["external_Kdenlive_PID_or_application_process_attested"])
            self.assertFalse(result["reopened_in_distinct_editor_process"])
            self.assertFalse(result["human_rights_or_visual_approval"])

    def test_native_saved_reopen_content_equivalence_not_process_provenance(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            reopen, reopen_sha = native_file(
                bundle, project.read_bytes(), "reopened.kdenlive",
            )
            a = inspect_saved_kdenlive(project, sha, bundle, seal)
            b = inspect_saved_kdenlive(reopen, reopen_sha, bundle, seal)
            result = compare_two_native_custody_snapshots(a, b)
            self.assertTrue(result["native_saved_project_byte_identical"])
            self.assertTrue(result["reopened_MLT_editorial_source_and_media_match"])
            self.assertEqual(result["application_process_reopen_authentication"],
                             "REQUIRES_DISTINCT_REAL_KDENLIVE_G2")

    def test_native_project_cannot_drop_two_source_clips_while_reusing_one_media(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            manifest_path = bundle / "bridge-manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["scene_order"] = ["scene-original-1", "scene-original-2", "scene-original-3"]
            manifest["bound_media"] = [
                {**manifest["bound_media"][0], "scene_id": item}
                for item in manifest["scene_order"]
            ]
            raw = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
            manifest_path.write_bytes(raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "lost_original_clip_instances"
            ):
                inspect_saved_kdenlive(project, sha, bundle, SHA(raw))

    def test_native_reopen_at_wrong_frame_clock_cannot_pass(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            reopened, reopen_sha = native_file(
                bundle, mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4", rate=24),
                "reopened.kdenlive",
            )
            a = inspect_saved_kdenlive(project, sha, bundle, seal)
            self.assertEqual(a["native_saved_frame_rate"], "30/1")
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "inpoint_or_SHA_no_longer_bound"
            ):
                inspect_saved_kdenlive(reopened, reopen_sha, bundle, seal)

    def test_Kdenlive_audio_companion_track_is_not_a_duplicate_video_clip(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<track producer="timeline0"/>',
                b'<track producer="timeline0"/>'
                b'<track hide="video" producer="audio_tractor"/>', 1,
            ).replace(
                b"</mlt>",
                b'<playlist id="audio_playlist">'
                b'<entry producer="chain1" in="0" out="29"/>'
                b'</playlist>'
                b'<tractor id="audio_tractor">'
                b'<track producer="audio_playlist"/></tractor></mlt>',
            )
            project, sha = native_file(bundle, raw)
            receipt = inspect_saved_kdenlive(project, sha, bundle, seal)
            self.assertEqual(receipt["observed_bound_clip_count"], 1)
            self.assertEqual(receipt["video_hidden_MLT_track_count_excluded"], 1)
            self.assertEqual(receipt["observed_bound_editorial_clip_semantics"][0][
                "original_media_sha256"], MEDIA_SHA)

    def test_duplicate_visible_playlist_occurrences_cannot_be_deduplicated(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<track producer="timeline0"/>',
                b'<track producer="timeline0"/><track producer="timeline0"/>', 1,
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "duplicate_visible_playback_container"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_black_entry_cannot_extend_the_original_editorial_cut_unobserved(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<entry producer="chain1" in="0" out="29"/>',
                b'<entry producer="chain1" in="0" out="29"/>'
                b'<entry producer="extra_black" in="0" out="29"/>', 1,
            ).replace(
                b"</mlt>",
                b'<producer id="extra_black">'
                b'<property name="mlt_service">color</property>'
                b'<property name="resource">0</property></producer></mlt>',
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "unbound_editorial_clip"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_parallel_scene_tracks_are_not_a_sequential_original_cut(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, _ = capsule(Path(scratch))
            manifest_path = bundle / "bridge-manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            manifest["bound_media"].append({
                **manifest["bound_media"][0],
                "scene_id": "scene-original-2", "source_start_frame": 30,
            })
            manifest["scene_order"].append("scene-original-2")
            manifest_raw = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
            manifest_path.write_bytes(manifest_raw)
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<track producer="timeline0"/>',
                b'<track producer="timeline0"/><track producer="timeline1"/>', 1,
            ).replace(
                b"</mlt>",
                b'<playlist id="timeline1">'
                b'<entry producer="chain1" in="30" out="59"/>'
                b'</playlist></mlt>',
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "parallel_editorial_video_tracks"
            ):
                inspect_saved_kdenlive(project, sha, bundle, SHA(manifest_raw))

    def test_bounded_black_base_and_nested_video_track_preserve_original_cut(self):
        # Both graph forms are synthetic contract fixtures, not editor receipts.
        for background_kind in ("producer", "playlist"):
            with self.subTest(background=background_kind):
                with tempfile.TemporaryDirectory() as scratch:
                    bundle, seal = capsule(Path(scratch))
                    background = (
                        b'<producer id="black" in="0" out="29">'
                        b'<property name="mlt_service">color</property>'
                        b'<property name="resource">0</property></producer>'
                    )
                    background_id = "black"
                    if background_kind == "playlist":
                        background += (
                            b'<playlist id="background">'
                            b'<entry producer="black" in="0" out="29"/>'
                            b'</playlist>'
                        )
                        background_id = "background"
                    raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                        b'<playlist id="timeline0">',
                        b'<playlist id="timeline0" in="0" out="29">', 1,
                    ).replace(
                        b'<tractor id="final_tractor">',
                        b'<tractor id="final_tractor" in="0" out="29">', 1,
                    ).replace(
                        b'<track producer="timeline0"/>',
                        (f'<track producer="{background_id}"/>'
                         '<track producer="video_tractor" in="0" out="29"/>'
                         ).encode("utf-8"), 1,
                    ).replace(
                        b"</mlt>",
                        background
                        + b'<playlist id="empty_video"/>'
                        b'<tractor id="video_tractor" in="0" out="29">'
                        b'<track producer="empty_video"/>'
                        b'<track producer="timeline0" in="0" out="29"/>'
                        b'</tractor></mlt>',
                    )
                    project, sha = native_file(bundle, raw)
                    receipt = inspect_saved_kdenlive(project, sha, bundle, seal)
                    self.assertEqual(receipt["authorized_black_background_track_count"], 1)
                    self.assertEqual(receipt["observed_editable_timeline_entry_count"], 1)
                    self.assertEqual(receipt["observed_bound_clip_count"], 1)
                    self.assertFalse(receipt["human_rights_or_visual_approval"])

    def test_playback_out_property_cannot_hide_a_container_or_entry_trim(self):
        for control in (
            b'<property name="out">14</property>',
            b'<property name="out" value="14"/>',
        ):
            replacements = (
                (b'<track producer="timeline0"/>',
                 b'<track producer="timeline0">' + control + b'</track>'),
                (b'</playlist>', control + b'</playlist>'),
                (b'</tractor>', control + b'</tractor>'),
                (b'<entry producer="chain1" in="0" out="29"/>',
                 b'<entry producer="chain1" in="0" out="29">'
                 + control + b'</entry>'),
            )
            for old, new in replacements:
                with self.subTest(control=control, node=old):
                    with tempfile.TemporaryDirectory() as scratch:
                        bundle, seal = capsule(Path(scratch))
                        raw = mlt_xml(
                            media_name=f"media/{MEDIA_SHA}.mp4",
                        ).replace(old, new, 1)
                        project, sha = native_file(bundle, raw)
                        with self.assertRaisesRegex(
                            KdenliveReadbackRejected, "playback_property_override"
                        ):
                            inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_track_hide_property_cannot_supply_visible_video_evidence(self):
        for control in (
            b'<property name="hide">video</property>',
            b'<property name="hide" value="video"/>',
        ):
            with self.subTest(control=control):
                with tempfile.TemporaryDirectory() as scratch:
                    bundle, seal = capsule(Path(scratch))
                    raw = mlt_xml(
                        media_name=f"media/{MEDIA_SHA}.mp4",
                    ).replace(
                        b'<track producer="timeline0"/>',
                        b'<track producer="timeline0">' + control + b'</track>',
                        1,
                    )
                    project, sha = native_file(bundle, raw)
                    with self.assertRaisesRegex(
                        KdenliveReadbackRejected, "playback_property_override"
                    ):
                        inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_entry_repeat_cannot_multiply_original_editorial_duration(self):
        for repeat in (0, 1, 2):
            with self.subTest(repeat=repeat):
                with tempfile.TemporaryDirectory() as scratch:
                    bundle, seal = capsule(Path(scratch))
                    raw = mlt_xml(
                        media_name=f"media/{MEDIA_SHA}.mp4",
                    ).replace(
                        b'<entry producer="chain1" in="0" out="29"/>',
                        (f'<entry producer="chain1" in="0" out="29" '
                         f'repeat="{repeat}"/>').encode("ascii"),
                        1,
                    )
                    project, sha = native_file(bundle, raw)
                    if repeat == 2:
                        with self.assertRaisesRegex(
                            KdenliveReadbackRejected, "editorial_entry_repeat"
                        ):
                            inspect_saved_kdenlive(project, sha, bundle, seal)
                    else:
                        receipt = inspect_saved_kdenlive(
                            project, sha, bundle, seal,
                        )
                        self.assertEqual(receipt["observed_bound_clip_count"], 1)
                        self.assertEqual(
                            receipt["observed_bound_editorial_clip_semantics"][0][
                                "duration_frames"], 30,
                        )

    def test_container_trim_cannot_impersonate_the_full_original_editorial_cut(self):
        for old, new in (
            (b'<tractor id="final_tractor">',
             b'<tractor id="final_tractor" in="15" out="29">'),
            (b'<track producer="timeline0"/>',
             b'<track producer="timeline0" in="0" out="14"/>'),
            (b'<playlist id="timeline0">',
             b'<playlist id="timeline0" in="15" out="29">'),
        ):
            with self.subTest(container=old):
                with tempfile.TemporaryDirectory() as scratch:
                    bundle, seal = capsule(Path(scratch))
                    raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                        old, new, 1,
                    )
                    project, sha = native_file(bundle, raw)
                    with self.assertRaisesRegex(
                        KdenliveReadbackRejected, "container_interval_not_full_editorial_span"
                    ):
                        inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_original_Kdenlive_video_track_gap_never_appears_as_a_contiguous_cut(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b"</playlist>",
                b'<blank length="00:00:01.000"/></playlist>', 1,
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "unexpected_gap"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_Kdenlive_media_on_audio_only_track_cannot_fake_video_reopen(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<track producer="timeline0"/>',
                b'<track hide="video" producer="timeline0"/>', 1,
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "lost_original_clip_instances"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_first_Kdenlive_import_may_not_claim_original_authored_reorder(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            observed = inspect_saved_kdenlive(project, sha, bundle, seal)
            observed["original_capsule_scene_order"] = ["another-original-scene"]
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "first_import_reordered"
            ):
                compare_two_native_custody_snapshots(observed, observed)

    def test_native_end_trim_survives_reopening_without_creating_new_source(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, original, original_sha = self.scene(Path(scratch))
            original_readback = inspect_saved_kdenlive(
                original, original_sha, bundle, seal
            )
            cut = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<entry producer="chain1" in="0" out="29"/>',
                b'<entry producer="chain1" in="0" out="14"/>', 1,
            )
            reopened, reopened_sha = native_file(bundle, cut, "reopened.kdenlive")
            later = inspect_saved_kdenlive(reopened, reopened_sha, bundle, seal)
            change = compare_two_native_custody_snapshots(original_readback, later)
            self.assertEqual(change["bounded_native_end_trims_observed"], [{
                "scene_id": "scene-original-1",
                "initial_duration_frames": 30,
                "reopened_duration_frames": 15,
            }])
            self.assertEqual(change["reopened_native_scene_order"],
                             ["scene-original-1"])
            self.assertFalse(change["external_editor_or_human_acceptance"])

    def test_native_clip_source_inpoint_shift_must_not_be_accepted_as_trim(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            wrong = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<entry producer="chain1" in="0" out="29"/>',
                b'<entry producer="chain1" in="1" out="29"/>', 1,
            )
            project, sha = native_file(bundle, wrong)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "inpoint_or_SHA_no_longer_bound"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_native_clip_duration_extension_is_not_a_bounded_end_trim(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            extended = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<entry producer="chain1" in="0" out="29"/>',
                b'<entry producer="chain1" in="0" out="39"/>', 1,
            )
            project, sha = native_file(bundle, extended)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "duration_extended"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_exact_original_MLT_inclusive_timecode_clock_and_rounding(self):
        from kdenlive_readback import original_mlt_frame
        self.assertEqual(original_mlt_frame("29", 30), 29)
        self.assertEqual(original_mlt_frame("00:00:00.967", 30), 29)
        self.assertEqual(original_mlt_frame("00:00:00:29", 30), 29)
        self.assertEqual(original_mlt_frame("00:00:01.000", 30), 30)
        for bad in ("00:00:00.968", "00:00:00:30", "0:0:0.0", "-1", "NaN"):
            with self.subTest(bad=bad):
                with self.assertRaises(KdenliveReadbackRejected):
                    original_mlt_frame(bad, 30)

    def test_original_explicit_missing_reference_cannot_be_promoted_to_full_NLE(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch), missing=True)
            with self.assertRaisesRegex(KdenliveReadbackRejected, "MissingReference"):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_native_media_path_escape_refused_even_if_extension_is_mp4(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(
                Path(scratch), foreign_media="../different-owner.mp4",
            )
            with self.assertRaisesRegex(KdenliveReadbackRejected, "outside_original_bundle"):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_original_source_file_digest_change_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            project.write_bytes(project.read_bytes() + b"\n")
            with self.assertRaisesRegex(KdenliveReadbackRejected, "expected_SHA"):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_native_xml_doctype_entity_and_xinclude_refused(self):
        for injected in (
            b'<!DOCTYPE mlt [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
            b'<xi:include href="file:///etc/passwd"/>',
            b'<?xml-stylesheet href="http://foreign/">',
        ):
            with self.subTest(kind=injected[:10]):
                with self.assertRaisesRegex(KdenliveReadbackRejected, "forbidden"):
                    safe_mlt_xml(injected + b"<mlt/>")

    def test_native_xml_unbound_namespace_shim_in_memory_only(self):
        text = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4", namespace_error=True)
        root, shimmed = safe_mlt_xml(text)
        self.assertTrue(shimmed)
        self.assertEqual(root.tag, "mlt")
        self.assertIn(b"kdenlive:compat", text)

    def test_missing_main_bin_editor_tractor_or_track_rejected(self):
        for knob in ("missing_bin", "missing_tractor", "missing_timeline"):
            with self.subTest(knob=knob), tempfile.TemporaryDirectory() as scratch:
                bundle, seal, project, sha = self.scene(Path(scratch), **{knob: True})
                with self.assertRaises(KdenliveReadbackRejected):
                    inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_duplicate_native_MLT_component_identifiers_rejected(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch), duplicate_ids=True)
            with self.assertRaisesRegex(KdenliveReadbackRejected, "duplicate_or_missing"):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_fake_Kdenlive_project_bytes_are_not_a_native_MLT_file(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, _, _ = self.scene(Path(scratch))
            fake, sha = native_file(bundle, b'{"OTIO_SCHEMA":"Timeline.1"}',
                                    "fake.kdenlive")
            with self.assertRaisesRegex(KdenliveReadbackRejected, "XML_not_parseable"):
                inspect_saved_kdenlive(fake, sha, bundle, seal)

    def test_unused_project_bin_media_cannot_impersonate_a_timeline_clip(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4")
            self.assertIn(b'<entry producer="chain1" in="0" out="29"/>', raw)
            # The original media remains in the project bin, but a different
            # color producer is what the editorial timeline actually plays.
            raw = raw.replace(
                b'<entry producer="chain1" in="0" out="29"/>',
                b'<entry producer="black" in="0" out="29"/>', 1,
            ).replace(b"</mlt>", b'<producer id="black"/></mlt>')
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "reachable_timeline_source_missing"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_broken_original_playback_producer_link_is_not_a_native_reopen(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<entry producer="chain1" in="0" out="29"/>',
                b'<entry producer="foreign-missing" in="0" out="29"/>', 1,
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "dangling_producer"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_cyclic_native_playback_graph_is_not_an_editable_timeline(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal = capsule(Path(scratch))
            raw = mlt_xml(media_name=f"media/{MEDIA_SHA}.mp4").replace(
                b'<track producer="timeline0"/>',
                b'<track producer="final_tractor"/>', 1,
            )
            project, sha = native_file(bundle, raw)
            with self.assertRaisesRegex(
                KdenliveReadbackRejected, "cycle_or_excessive_depth"
            ):
                inspect_saved_kdenlive(project, sha, bundle, seal)

    def test_native_Kdenlive_file_symlink_observation_denied_on_all_hosts(self):
        with tempfile.TemporaryDirectory() as scratch:
            bundle, seal, project, sha = self.scene(Path(scratch))
            old = Path.is_symlink
            with mock.patch.object(Path, "is_symlink",
                                   lambda item: item == project or old(item)):
                with self.assertRaisesRegex(KdenliveReadbackRejected, "plain_absolute"):
                    inspect_saved_kdenlive(project, sha, bundle, seal)




class KdenliveRetainedAlabReadbackContracts(unittest.TestCase):
    """Synthetic XML/media/notices through the existing retained bridge; no NLE."""

    def fixture(self):
        import bridge
        import test_bridge as controls
        helper = controls.RetainedAlabObservationTests()
        self.addCleanup(helper.doCleanups)
        root, owner, bundle, project, request, retained, data = helper.portable()
        bridge.export_portable(owner, request, bundle, retained_alab=retained,
                               source_notices=helper.source_notices)
        root, bundle = root.resolve(strict=True), bundle.resolve(strict=True)
        manifest_sha = SHA((bundle / "bridge-manifest.json").read_bytes())
        return root, bundle, manifest_sha, project, retained, data

    def xml(self, data, *, second=182, rate=24, width=1920, height=1080,
            first_in=0, extension="mov"):
        sources = []
        for index, row in enumerate(data["media_pins"]):
            sources.append(f'<chain id="video{index}"><property name="resource">'
                           f'media/{row[3]}.{extension}</property></chain>')
        return (f'<mlt><profile width="{width}" height="{height}" frame_rate_num="{rate}" '
                f'frame_rate_den="1"/>' + ''.join(sources)
                + '<playlist id="main_bin"><entry producer="video0" in="0" out="51"/>'
                  '<entry producer="video1" in="0" out="181"/></playlist>'
                + f'<playlist id="timeline0"><entry producer="video0" in="{first_in}" out="51"/>'
                  f'<entry producer="video1" in="0" out="{second - 1}"/></playlist>'
                  '<tractor id="main_tractor"><property name="kdenlive:projectTractor">1</property>'
                  '<track producer="timeline0"/></tractor></mlt>').encode()

    def test_two_retained_MOV_instances_keep_24fps_52_182_and_only_12frame_second_trim(self):
        import bridge
        _, bundle, manifest_sha, project, retained, data = self.fixture()
        initial, initial_sha = native_file(bundle, self.xml(data), "initial.kdenlive")
        edited, edited_sha = native_file(bundle, self.xml(data, second=170), "edited.kdenlive")
        with mock.patch.object(bridge, "file_probe", side_effect=AssertionError("no retained decode")) as probe, \
             mock.patch.object(bridge.subprocess, "Popen", side_effect=AssertionError("no native child")) as child:
            before = inspect_saved_kdenlive(initial, initial_sha, bundle, manifest_sha, retained_alab=retained)
            after = inspect_saved_kdenlive(edited, edited_sha, bundle, manifest_sha, retained_alab=retained)
            observed = compare_two_native_custody_snapshots(before, after)
            probe.assert_not_called(); child.assert_not_called()
        self.assertEqual(before["native_saved_frame_rate"], "24/1")
        self.assertEqual(before["native_saved_profile_size"], [1920, 1080])
        self.assertEqual([r["duration_frames"] for r in before["observed_bound_editorial_clip_semantics"]], [52, 182])
        self.assertEqual([r["duration_frames"] for r in after["observed_bound_editorial_clip_semantics"]], [52, 170])
        self.assertEqual([r["original_inpoint_frame"] for r in before["observed_bound_editorial_clip_semantics"]], [0, 0])
        self.assertEqual(observed["bounded_native_end_trims_observed"], [{"scene_id": project["scenes"][1]["id"],
            "initial_duration_frames": 182, "reopened_duration_frames": 170}])
        self.assertFalse(after["external_Kdenlive_PID_or_application_process_attested"])
        self.assertFalse(after["reopened_in_distinct_editor_process"])
        self.assertFalse(after["human_rights_or_visual_approval"])
        self.assertEqual(retained.descriptor()["historical_source_origin"], "NOT_OBSERVED")

    def test_saved_MOV_project_cannot_self_admit_a_profile_from_manifest_or_descriptor(self):
        _, bundle, manifest_sha, _, retained, data = self.fixture()
        initial, initial_sha = native_file(bundle, self.xml(data))
        for value in (None, retained.descriptor(), {"verified": True}):
            with self.subTest(value_type=type(value).__name__), \
                 mock.patch("kdenlive_readback.safe_mlt_xml", side_effect=AssertionError("no XML before source admission")) as parser:
                with self.assertRaises(KdenliveReadbackRejected):
                    inspect_saved_kdenlive(initial, initial_sha, bundle, manifest_sha, retained_alab=value)
                parser.assert_not_called()

    def test_retained_native_XML_cannot_change_clock_geometry_inpoint_container_or_source(self):
        import bridge
        for defect in ("fps", "width", "height", "inpoint", "extension", "media"):
            with self.subTest(defect=defect):
                _, bundle, manifest_sha, _, retained, data = self.fixture()
                values = {"fps": {"rate": 30}, "width": {"width": 1280}, "height": {"height": 720},
                          "inpoint": {"first_in": 1}, "extension": {"extension": "mp4"}, "media": {}}[defect]
                initial, initial_sha = native_file(bundle, self.xml(data, **values))
                if defect == "media":
                    (bundle / f"media/{data['media_pins'][0][3]}.mov").write_bytes(b"foreign source")
                with mock.patch.object(bridge, "file_probe", side_effect=AssertionError("invalid source decoder")) as probe:
                    with self.assertRaises(KdenliveReadbackRejected):
                        inspect_saved_kdenlive(initial, initial_sha, bundle, manifest_sha, retained_alab=retained)
                    probe.assert_not_called()

    def test_saved_native_project_does_not_erase_original_notice_requirements(self):
        import bridge
        for role in ("license", "readme", "selection_notice"):
            for defect in ("missing", "changed"):
                with self.subTest(role=role, defect=defect):
                    _, bundle, manifest_sha, _, retained, data = self.fixture()
                    initial, initial_sha = native_file(bundle, self.xml(data))
                    name = next(row[1] for row in bridge._ALAB_SOURCE_NOTICES if row[0] == role)
                    if defect == "missing":
                        (bundle / name).unlink()
                    else:
                        (bundle / name).write_bytes(b"changed original notice")
                    with mock.patch("kdenlive_readback.safe_mlt_xml", side_effect=AssertionError("no XML after notice failure")) as parser:
                        with self.assertRaises(KdenliveReadbackRejected):
                            inspect_saved_kdenlive(initial, initial_sha, bundle, manifest_sha, retained_alab=retained)
                        parser.assert_not_called()


class ImmutableKdenliveOriginalReadTests(unittest.TestCase):
    def test_verified_read_uses_the_exact_captured_bytes_without_reopening_path(self):
        import ci_private_evidence as custody
        import kdenlive_readback as reader
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder).resolve()/"original.kdenlive"
            original=b'<mlt producer="original"/>'
            changed=b'<mlt producer="replaced"/>'
            path.write_bytes(original)
            real=custody.regular
            calls=[]
            def replace_after_capture(candidate,limit):
                result=real(candidate,limit)
                calls.append(candidate)
                path.write_bytes(changed)
                return result
            with mock.patch.object(custody,"regular",side_effect=replace_after_capture), \
                 mock.patch.object(Path,"read_bytes",side_effect=AssertionError("readback reopened pathname")):
                observed=reader.read_closed_bytes(path,SHA(original),1024)
            self.assertEqual(observed,original)
            self.assertEqual(calls,[path])
            self.assertEqual(path.read_bytes(),changed)

    def test_invalid_pin_wrong_SHA_and_growth_keep_the_exact_rejection_contract(self):
        import kdenlive_readback as reader
        from bridge import BridgeRefused
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder).resolve()/"original.json"
            path.write_bytes(b"original")
            with mock.patch.object(reader,"capture_original",side_effect=AssertionError("invalid pin read")):
                with self.assertRaisesRegex(KdenliveReadbackRejected,"readback_original_SHA256_missing"):
                    reader.read_closed_bytes(path,"not-a-digest",64)
            with self.assertRaisesRegex(KdenliveReadbackRejected,"readback_original_content_changed_from_expected_SHA"):
                reader.read_closed_bytes(path,"f"*64,64)
            with self.assertRaisesRegex(BridgeRefused,"File exceeds byte budget:"):
                reader.read_closed_bytes(path,SHA(b"original"),2)

    def test_capture_primary_error_or_interrupt_is_never_replaced_by_parse_or_reopen(self):
        import kdenlive_readback as reader
        with tempfile.TemporaryDirectory()as folder:
            path=Path(folder).resolve()/"original.json"
            path.write_bytes(b"original")
            for original in (OSError("original IO"),KeyboardInterrupt("original interrupt"),SystemExit(17)):
                with self.subTest(kind=type(original).__name__), \
                     mock.patch.object(reader,"capture_original",side_effect=original), \
                     mock.patch.object(Path,"read_bytes",side_effect=AssertionError("secondary reopen")):
                    with self.assertRaises(type(original))as caught:
                        reader.read_closed_bytes(path,SHA(b"original"),64)
                    self.assertIs(caught.exception,original)


if __name__ == "__main__":
    unittest.main()
