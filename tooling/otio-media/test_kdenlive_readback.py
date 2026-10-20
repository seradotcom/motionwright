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


if __name__ == "__main__":
    unittest.main()
