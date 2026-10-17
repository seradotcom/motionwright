"""Real tiny RGBA source-inspection fixtures, NOT a simulated native editor."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_motion_canvas_roundtrip as mc


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
GENERATION = "22222222-2222-4222-8222-222222222222"
SCENE_ID = "33333333-3333-4333-8333-333333333333"
NODE_ID = "44444444-4444-4444-8444-444444444444"


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def png(rgb: bytes, note: bytes = b"") -> bytes:
    assert len(rgb) == 12
    def chunk(name: bytes, body: bytes) -> bytes:
        data = name + body
        return struct.pack(">I", len(body)) + data + struct.pack(">I", zlib.crc32(data))
    pixels = b"\x00" + rgb[:6] + b"\x00" + rgb[6:]
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + (chunk(b"tEXt", b"note\x00" + note) if note else b"")
        + chunk(b"IEND", b"")
    )


def record(root: Path, relative: str, data: bytes) -> dict:
    dest = root / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"path": relative, "bytes": len(data), "sha256": sha(data)}


def replace_record(root: Path, witness: dict, stage: str, kind: str, data: bytes) -> None:
    entry = witness["stages"][stage][kind]
    path = root / entry["path"]
    path.write_bytes(data)
    entry["bytes"] = len(data)
    entry["sha256"] = sha(data)


def fixture(root: Path) -> dict:
    before = {
        "id": PROJECT_ID, "generation": GENERATION, "revision": 4,
        "updated_at": "2026-10-17T12:00:00Z",
        "assets": [{"id": "55555555-5555-4555-8555-555555555555",
                    "rights": "fixture-only", "content_sha256": "a" * 64}],
        "deliverables": [{"id": "66666666-6666-4666-8666-666666666666",
                          "width": 2, "height": 2, "frame_rate": "30/1"}],
        "scenes": [{"id": SCENE_ID, "duration": {"num": 1, "den": 10},
                    "nodes": [{"id": NODE_ID, "name": "fixture rectangle",
                               "x": 4, "keyframes": [{"property": "x", "at": 0, "value": 4}],
                               "style": {"fill": "#F5F5F2", "opacity": 1.0}}]}]
    }
    after = copy.deepcopy(before)
    after["revision"] = 5
    after["updated_at"] = "2026-10-17T12:01:00Z"
    after["scenes"][0]["nodes"][0]["style"]["fill"] = "#E85A35"
    original_film = {"output": {"width": 2, "height": 2,
                                "frame_rate": {"num": 30, "den": 1}},
                     "sequences": [{"subject_id": NODE_ID, "color": "#F5F5F2",
                                    "frames": 3, "clock": "30/1"}]}
    edited_film = copy.deepcopy(original_film)
    edited_film["sequences"][0]["color"] = "#E85A35"
    witness = {
        "schema": mc.SCHEMA, "profile": {"width": 2, "height": 2, "fps": "30/1", "frames": 3},
        "edit": {"scene_id": SCENE_ID, "node_id": NODE_ID, "property": "style.fill",
                 "native_scene_id": "scene-a", "native_node_id": "tile-a",
                 "native_property": "fill", "from": "#F5F5F2", "to": "#E85A35"},
        "stages": {},
    }
    for i, stage in enumerate(mc.STAGES):
        is_edited = stage.startswith("edited")
        project = after if is_edited else before
        film = edited_film if is_edited else original_film
        source_content = b"renderNode({fill: '#E85A35'})" if is_edited else b"renderNode({fill: '#F5F5F2'})"
        entry = {
            "session": "unique-native-session-" + str(i),
            "native_job_ref": "separate-native-job-" + str(i),
            "project": record(root, f"{stage}/project.json",
                              (json.dumps(project, sort_keys=True) + "\n").encode()),
            "film": record(root, f"{stage}/film.json",
                           (json.dumps(film, sort_keys=True) + "\n").encode()),
            "source_files": [
                {"role": "native/source/materialized.ts",
                 **record(root, f"{stage}/native/materialized.ts", source_content)}
            ],
            "frames_dir": f"{stage}/render",
        }
        # The pinned Semwright driver has a separate managed Project wire:
        # 32-hex generation and properties.fill (not Motionwright style.fill).
        managed_project = {
            "id": "managed-mc-source", "generation": "a" * 32,
            "schema_version": 1, "component_version": 1,
            "revision": 12 if is_edited else 11,
            "settings": {"width": 2, "height": 2, "fps": 30, "fps_denominator": 1},
            "scenes": [{"id": "scene-a", "name": "Main", "duration_ms": 100,
                        "nodes": [{"id": "tile-a", "name": "Tile",
                                   "kind": "rect",
                                   "properties": {"fill": "#E85A35" if is_edited else "#F5F5F2"}}],
                        "animations": [], "cues": [], "transition": None}],
            "assets": [], "audio": [], "variables": {},
            "authoring": {
                "schema_version": 1,
                "intent": copy.deepcopy(film),
                "intent_digest": "d" * 64,
                "realization": {"version": 1, "scenes": [], "schedule": {},
                                "instructions": []},
                "realization_digest": "e" * 64,
                "projection_digest": "f" * 64,
            },
        }
        native_source = entry["source_files"][0]
        managed = {
            "project": managed_project, "refs": [],
            "fingerprint": ("b" if is_edited else "a") * 64,
            "generated": [{"path": native_source["role"], "bytes": native_source["bytes"],
                           "sha256": native_source["sha256"]}],
        }
        entry["managed_inspection"] = record(
            root, f"{stage}/native/project-inspect.json",
            (json.dumps(managed, sort_keys=True) + "\n").encode())
        frames = root / stage / "render" / "frames"
        frames.mkdir(parents=True)
        manifest_rows = []
        for at in range(3):
            channel = (180 + at * 20) if is_edited else (20 + at * 20)
            rgb = bytes(([0, channel, 0] if is_edited else [channel, 0, 0]) * 4)
            data = png(rgb, note=f"{stage}-{at}".encode())
            relative = f"frames/{at:06d}.png"
            (root / stage / "render" / relative).write_bytes(data)
            manifest_rows.append({"index": at, "file": relative, "bytes": len(data),
                                  "sha256": sha(data)})
        (root / stage / "render" / "artifact-manifest.json").write_text(json.dumps({
            "plan": {"first_frame": 0, "end_frame_exclusive": 3, "frame_count": 3},
            "frames": manifest_rows
        }))
        witness["stages"][stage] = entry
    return witness


class PureRoundtripContractTests(unittest.TestCase):
    """Reader/source admission only: no subprocess, no native execution."""

    def test_exact_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for data, reason in (
            (b'{"revision":1,"revision":1}', "duplicate_json_key"),
            (b'{"a":NaN}', "nonfinite_json_value"),
            (b'{"a":Infinity}', "nonfinite_json_value"),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    mc.RoundtripRefused, reason):
                mc.checked_json(data)
        self.assertEqual(mc.checked_json(b'{"ok":true}'), {"ok": True})

    def test_production_profile_cannot_accept_three_frame_unit_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            witness = fixture(root)
            with self.assertRaisesRegex(mc.RoundtripRefused, "required_native_60frame_profile"):
                mc.inspect(root, witness)

    def test_canonical_change_is_one_revision_and_preserves_source_rights(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            witness = fixture(root)
            original = json.loads((root / witness["stages"]["original"]["project"]["path"]).read_text())
            edited = json.loads((root / witness["stages"]["edited"]["project"]["path"]).read_text())
            result = mc.assert_source_edit(original, edited, witness["edit"])
            self.assertEqual(result["edited_revision"], result["previous_revision"] + 1)
            forbidden = copy.deepcopy(edited)
            forbidden["assets"][0]["rights"] = "unauthorized"
            with self.assertRaisesRegex(mc.RoundtripRefused, "unrelated_intent_or_rights"):
                mc.assert_source_edit(original, forbidden, witness["edit"])

    def test_managed_project_and_generated_files_are_separate_pinned_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            witness = fixture(root)
            row = witness["stages"]["original"]
            inventory = mc.source_files(root, row["source_files"])
            film = json.loads((root / row["film"]["path"]).read_text())
            native = mc.managed_snapshot(root, row["managed_inspection"], inventory, 3, 2, 2, film)
            self.assertEqual(native["project"]["generation"], "a" * 32)
            self.assertNotEqual(native["project"]["generation"], GENERATION)
            self.assertEqual(len(native["generated"]), 1)
            tampered = json.loads((root / row["managed_inspection"]["path"]).read_text())
            tampered["generated"][0]["sha256"] = "0" * 64
            replace_record(root, witness, "original", "managed_inspection",
                           (json.dumps(tampered, sort_keys=True) + "\n").encode())
            with self.assertRaisesRegex(
                    mc.RoundtripRefused, "native_generated_source_does_not_match_retained_files"):
                mc.managed_snapshot(root, witness["stages"]["original"]["managed_inspection"],
                                    inventory, 3, 2, 2, film)

    def test_foreign_managed_authoring_intent_is_never_bound_to_canonical_film(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            witness = fixture(root)
            row = witness["stages"]["original"]
            film = json.loads((root / row["film"]["path"]).read_text())
            inventory = mc.source_files(root, row["source_files"])
            native = mc.checked_json(mc.bounded_file(root, row["managed_inspection"]))
            native["project"]["authoring"]["intent"]["sequences"][0]["color"] = "#000000"
            replace_record(root, witness, "original", "managed_inspection",
                           (json.dumps(native, sort_keys=True) + "\n").encode())
            with self.assertRaisesRegex(mc.RoundtripRefused,
                                        "managed_authoring_intent_not_bound_to_film"):
                mc.managed_snapshot(root, witness["stages"]["original"]["managed_inspection"],
                                    inventory, 3, 2, 2, film)

    def test_native_binding_requires_full_shape_and_digest_wire(self):
        for mutation, reason in (
            (lambda doc: doc["project"].update(authoring=None),
             "managed_authoring_binding_missing"),
            (lambda doc: doc["project"]["authoring"].update(intent_digest="0" * 63),
             "managed_authoring_digest_malformed"),
            (lambda doc: doc["project"]["authoring"].update(schema_version=True),
             "managed_authoring_version_mismatch"),
        ):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                witness = fixture(root)
                row = witness["stages"]["original"]
                film = json.loads((root / row["film"]["path"]).read_text())
                inventory = mc.source_files(root, row["source_files"])
                native = mc.checked_json(mc.bounded_file(root, row["managed_inspection"]))
                mutation(native)
                replace_record(root, witness, "original", "managed_inspection",
                               (json.dumps(native, sort_keys=True) + "\n").encode())
                with self.assertRaisesRegex(mc.RoundtripRefused, reason):
                    mc.managed_snapshot(root, witness["stages"]["original"]["managed_inspection"],
                                        inventory, 3, 2, 2, film)

    def test_relative_path_escape_and_file_truncation_fail_before_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            witness = fixture(root)
            for name in ("../private", "/absolute", "C:/other", "folder/../../escape"):
                with self.subTest(name=name), self.assertRaises(mc.RoundtripRefused):
                    mc.safe_path(root, name)
            record = witness["stages"]["original"]["source_files"][0]
            raw = {key: record[key] for key in ("path", "bytes", "sha256")}
            self.assertEqual(len(mc.bounded_file(root, raw)), record["bytes"])
            (root / record["path"]).write_bytes(b"short")
            with self.assertRaisesRegex(mc.RoundtripRefused, "physical_source_size_changed"):
                mc.bounded_file(root, raw)

    def test_native_managed_edit_rejects_reused_generation_and_unrelated_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            witness = fixture(root)
            one = witness["stages"]["original"]["managed_inspection"]
            two = witness["stages"]["edited"]["managed_inspection"]
            original = mc.checked_json(mc.bounded_file(root, one))
            edited = mc.checked_json(mc.bounded_file(root, two))
            mc.assert_managed_edit(original, edited, witness["edit"])
            wrong = copy.deepcopy(edited)
            wrong["project"]["generation"] = "f" * 32
            with self.assertRaisesRegex(mc.RoundtripRefused, "managed_edit_identity_or_revision_changed"):
                mc.assert_managed_edit(original, wrong, witness["edit"])


@unittest.skipUnless(
    shutil.which("ffmpeg"),
    "Independent FFmpeg required: execute these 20 physical tests in the A6 multimedia lane; never count skips as acceptance",
)
class RealSourceAndFrameInspection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.witness = fixture(self.root)

    def inspect(self):
        return mc.inspect(self.root, self.witness, _unit_fixture=True)

    def test_original_reopen_edit_reopen_all_three_real_frames(self):
        report = self.inspect()
        self.assertEqual(report["all_frames_before_after_and_both_reopens_checked"], 3)
        self.assertTrue(report["all_frames_changed_after_declared_fill_edit"])
        self.assertFalse(report["provider_authority_verified"])
        self.assertFalse(report["human_visual_approval"])
        self.assertEqual(report["edit"]["edited_revision"], 5)
        self.assertEqual(len(report["decoded_rgba_digest_chain_by_stage"]), 4)

    def test_wrong_clock_geometry_and_bool_count_rejected_before_decode(self):
        for alteration in (
            lambda v: v["profile"].update(frames=3.0),
            lambda v: v["profile"].update(frames=True),
            lambda v: v["profile"].update(fps="30000/1001"),
            lambda v: v["profile"].update(width=3),
        ):
            with self.subTest(change=alteration):
                witness = copy.deepcopy(self.witness)
                alteration(witness)
                with self.assertRaises(mc.RoundtripRefused):
                    mc.inspect(self.root, witness, _unit_fixture=True)

    def test_foreign_or_reused_reopen_session_and_job_rejected(self):
        for field in ("session", "native_job_ref"):
            with self.subTest(field=field):
                witness = copy.deepcopy(self.witness)
                witness["stages"]["reopened"][field] = witness["stages"]["original"][field]
                with self.assertRaisesRegex(mc.RoundtripRefused, "reused|fresh_session"):
                    mc.inspect(self.root, witness, _unit_fixture=True)

    def test_modified_source_after_readback_fails_even_with_new_bound_sha(self):
        stage = "reopened"
        row = self.witness["stages"][stage]["source_files"][0]
        data = b"renderNode({fill: '#FFFFFF'})"
        dest = self.root / row["path"]
        dest.write_bytes(data)
        row["sha256"] = sha(data)
        row["bytes"] = len(data)
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "native_generated_source_does_not_match_retained_files|native_source_changed"):
            self.inspect()

    def test_edited_source_did_not_persist_is_not_a_success(self):
        old = self.root / self.witness["stages"]["original"]["source_files"][0]["path"]
        for stage in ("edited", "edited_reopened"):
            row = self.witness["stages"][stage]["source_files"][0]
            data = old.read_bytes()
            (self.root / row["path"]).write_bytes(data)
            row["sha256"] = sha(data)
            row["bytes"] = len(data)
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "native_generated_source_does_not_match_retained_files|native_source_did_not_change"):
            self.inspect()

    def test_edit_must_not_modify_original_rights_and_assets(self):
        for stage in ("edited", "edited_reopened"):
            row = self.witness["stages"][stage]["project"]
            project = json.loads((self.root / row["path"]).read_text())
            project["assets"][0]["rights"] = "forged-new-rights"
            replace_record(self.root, self.witness, stage, "project",
                           (json.dumps(project, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(mc.RoundtripRefused, "unrelated_intent_or_rights"):
            self.inspect()

    def test_no_op_edit_or_discarded_project_revision_rejected(self):
        for stage in ("edited", "edited_reopened"):
            row = self.witness["stages"][stage]["project"]
            project = json.loads((self.root / row["path"]).read_text())
            project["revision"] = 4
            replace_record(self.root, self.witness, stage, "project",
                           (json.dumps(project, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(mc.RoundtripRefused, "revision_did_not_advance"):
            self.inspect()

    def test_source_edit_not_present_in_film_is_refused(self):
        original = (self.root / self.witness["stages"]["original"]["film"]["path"]).read_bytes()
        for stage in ("edited", "edited_reopened"):
            replace_record(self.root, self.witness, stage, "film", original)
        with self.assertRaisesRegex(
                mc.RoundtripRefused,
                "managed_authoring_intent_not_bound_to_film|source_edit_not_realized_in_film"):
            self.inspect()

    def test_changed_frame_is_required_for_every_saved_fill_edit_index(self):
        for stage in ("edited", "edited_reopened"):
            source = self.root / self.witness["stages"]["original"]["frames_dir"] / "frames/000001.png"
            dest = self.root / self.witness["stages"][stage]["frames_dir"] / "frames/000001.png"
            data = source.read_bytes()
            dest.write_bytes(data)
            manifest = self.root / self.witness["stages"][stage]["frames_dir"] / "artifact-manifest.json"
            doc = json.loads(manifest.read_text())
            doc["frames"][1]["sha256"] = sha(data)
            doc["frames"][1]["bytes"] = len(data)
            manifest.write_text(json.dumps(doc))
        with self.assertRaisesRegex(mc.RoundtripRefused, "edited_pixels_unchanged_at_frame_1"):
            self.inspect()

    def test_same_decoded_pixels_with_corrupt_png_crc_and_rehashed_manifest_are_refused(self):
        # A forged manifest cannot replace a physically sound PNG. Original
        # FFmpeg7.1 returns the same RGBA pixels for this bad middle IDAT CRC.
        stage = "reopened"
        root = self.root / self.witness["stages"][stage]["frames_dir"]
        path = root / "frames/000001.png"
        data = bytearray(path.read_bytes())
        offset = 8
        while offset < len(data):
            size = struct.unpack(">I", data[offset:offset + 4])[0]
            kind = data[offset + 4:offset + 8]
            if kind == b"IDAT":
                data[offset + 8 + size] ^= 1
                break
            offset += 12 + size
        else:
            self.fail("the fixture must have an IDAT chunk")
        path.write_bytes(data)
        manifest = root / "artifact-manifest.json"
        doc = json.loads(manifest.read_text())
        doc["frames"][1]["sha256"] = sha(data)
        manifest.write_text(json.dumps(doc))
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "corrupt_png_crc|native_frame_integrity_or_decode_failed"):
            self.inspect()

    def test_native_managed_edit_must_persist_in_original_driver_schema(self):
        for stage in ("edited", "edited_reopened"):
            row = self.witness["stages"][stage]["managed_inspection"]
            snapshot = json.loads((self.root / row["path"]).read_text())
            snapshot["project"]["scenes"][0]["nodes"][0]["properties"]["fill"] = "#F5F5F2"
            replace_record(self.root, self.witness, stage, "managed_inspection",
                           (json.dumps(snapshot, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "managed_node_property_not_persisted"):
            self.inspect()

    def test_managed_generated_inventory_cannot_lie_about_source_file(self):
        for stage in ("reopened",):
            row = self.witness["stages"][stage]["managed_inspection"]
            snapshot = json.loads((self.root / row["path"]).read_text())
            snapshot["generated"][0]["sha256"] = "0" * 64
            replace_record(self.root, self.witness, stage, "managed_inspection",
                           (json.dumps(snapshot, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "native_generated_source_does_not_match_retained_files"):
            self.inspect()

    def test_managed_generation_and_revision_are_separate_from_motionwright_identity(self):
        for stage in ("edited", "edited_reopened"):
            row = self.witness["stages"][stage]["managed_inspection"]
            snapshot = json.loads((self.root / row["path"]).read_text())
            snapshot["project"]["generation"] = "c" * 32
            replace_record(self.root, self.witness, stage, "managed_inspection",
                           (json.dumps(snapshot, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "managed_edit_identity_or_revision_changed"):
            self.inspect()

    def test_native_driver_clock_must_match_canonical_film(self):
        stage = "original"
        row = self.witness["stages"][stage]["managed_inspection"]
        snapshot = json.loads((self.root / row["path"]).read_text())
        snapshot["project"]["settings"]["fps"] = 60
        replace_record(self.root, self.witness, stage, "managed_inspection",
                       (json.dumps(snapshot, sort_keys=True) + "\n").encode())
        with self.assertRaisesRegex(mc.RoundtripRefused,
                                    "managed_renderer_profile_differs_from_film"):
            self.inspect()

    def test_missing_frame_and_undeclared_extra_frame_refused(self):
        frame = self.root / self.witness["stages"]["edited"]["frames_dir"] / "frames/000002.png"
        original = frame.read_bytes()
        frame.unlink()
        with self.assertRaisesRegex(mc.RoundtripRefused, "extra_or_missing_native_frame"):
            self.inspect()
        frame.write_bytes(original)
        (frame.parent / "000003.png").write_bytes(original)
        with self.assertRaisesRegex(mc.RoundtripRefused, "extra_or_missing_native_frame"):
            self.inspect()

    def test_invalid_duplicate_fields_and_nan_source_json_rejected(self):
        row = self.witness["stages"]["original"]["project"]
        original = (self.root / row["path"]).read_bytes()
        for marker in (b'"revision": 4, "revision": 4', b'"revision": NaN'):
            with self.subTest(marker=marker):
                bad = original.replace(b'"revision": 4', marker, 1)
                replace_record(self.root, self.witness, "original", "project", bad)
                with self.assertRaisesRegex(mc.RoundtripRefused, "duplicate_json_key|nonfinite_json_value"):
                    self.inspect()

    def test_source_file_truncation_foreign_absolute_and_relative_escape_rejected(self):
        row = self.witness["stages"]["original"]["source_files"][0]
        (self.root / row["path"]).write_bytes(b"truncated")
        with self.assertRaisesRegex(mc.RoundtripRefused, "physical_source"):
            self.inspect()
        for path in ("../outside", "/absolute/path", "C:/untrusted", "sub/../../escape"):
            with self.subTest(path=path), self.assertRaises(mc.RoundtripRefused):
                mc.safe_path(self.root, path)

    def test_nonadmitted_edit_is_refused(self):
        witness = copy.deepcopy(self.witness)
        witness["edit"]["property"] = "camera.focal_length"
        with self.assertRaisesRegex(mc.RoundtripRefused, "unadmitted_source_edit"):
            mc.inspect(self.root, witness, _unit_fixture=True)

    def test_late_source_frame_mutation_after_decode_cannot_publish_receipt(self):
        real_reader = mc.native_png_hashes
        calls = []

        def changed_during_inspection(root, count):
            result = real_reader(root, count)
            calls.append(str(root))
            if len(calls) == 4:
                target = self.root / "original/render/frames/000001.png"
                target.write_bytes(target.read_bytes() + b"late-tampering")
            return result

        with mock.patch.object(mc, "native_png_hashes", side_effect=changed_during_inspection):
            with self.assertRaisesRegex(mc.RoundtripRefused,
                                        "native_frame_bytes_changed_after_decode"):
                self.inspect()
        self.assertEqual(len(calls), 4, "all four initial decodes completed; final verification refused the altered source")

    def test_production_cli_refuses_three_frame_unit_fixture_and_never_overwrites_output(self):
        witness = self.root / "witness.json"
        witness.write_text(json.dumps(self.witness, sort_keys=True), encoding="utf-8")
        report = self.root / "report.json"
        command = ["motion_canvas_roundtrip", "--root", str(self.root),
                   "--witness", "witness.json", "--output", str(report)]
        with mock.patch.object(sys, "argv", command):
            with self.assertRaisesRegex(mc.RoundtripRefused, "required_native_60frame_profile"):
                mc.main()
            self.assertFalse(report.exists())
            report.write_bytes(b"immutable prior receipt")
            with self.assertRaisesRegex(mc.RoundtripRefused, "new_report_destination_required"):
                mc.main()
            self.assertEqual(report.read_bytes(), b"immutable prior receipt")


if __name__ == "__main__":
    unittest.main()
