import argparse
import contextlib
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_camera_cycle as cycle


class SourceCycleCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            evidence_root=Path("/private/original"),
            sealed_originals=Path("/sealed/original.mwci"),
            edited_evidence_root=Path("/private/edited"),
            sealed_edited=Path("/sealed/edited.mwci"),
        )
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "12345", "run_attempt": "1"}
        self.validate = mock.Mock(return_value=({"sha256": "c" * 64}, self.context))
        self.original = {
            "evidence": self.args.evidence_root,
            "sealed_originals": self.args.sealed_originals,
        }
        self.edited = {
            "evidence": self.args.edited_evidence_root,
            "sealed_originals": self.args.sealed_edited,
            "cycle_observation": {
                "source_cycle_observation": "PASS_REQUIRES_SEALED_CUSTODY",
            },
        }
        self.phase = mock.Mock(side_effect=[self.original, self.edited])
        self.publish = mock.Mock()
        self.observed = mock.patch("native_manim_camera_evidence.read_object",
                                   return_value=self.edited["cycle_observation"]).start()
        self.addCleanup(mock.patch.stopall)

    def run_cycle(self):
        return cycle.run_source_cycle(
            self.args, run_phase=self.phase, validate_phase=self.validate,
            publish_phase=self.publish,
        )

    def test_two_distinct_scopes_and_edited_fixture_are_explicit(self):
        value = self.run_cycle()
        self.assertEqual(self.phase.call_count, 2)
        self.assertEqual(self.validate.call_count, 2)
        first, second = self.phase.call_args_list
        self.assertEqual(first.args[0].evidence_root, self.args.evidence_root)
        self.assertEqual(second.args[0].evidence_root, self.args.edited_evidence_root)
        self.assertIs(second.kwargs["source_cycle"], self.original)
        self.assertNotEqual(first.args[0].sealed_originals, second.args[0].sealed_originals)
        self.assertEqual(self.publish.call_args_list[0].kwargs,
                         {"scenario": "camera120-source-cycle", "current": self.context})
        self.assertEqual(self.publish.call_args_list[1].kwargs,
                         {"fixture": "edited1040", "scenario": "camera120-source-cycle",
                          "current": self.context})
        self.assertIs(value, self.edited["cycle_observation"])

    def test_unknown_original_stops_without_second_invocation(self):
        self.phase.side_effect = RuntimeError("native OutcomeUnknown")
        with self.assertRaisesRegex(RuntimeError, "OutcomeUnknown"):
            self.run_cycle()
        self.assertEqual(self.phase.call_count, 1)
        self.publish.assert_not_called()

    def test_incomplete_original_custody_stops_before_edit(self):
        self.publish.side_effect = ValueError("camera_custody_incomplete")
        with self.assertRaisesRegex(ValueError, "custody_incomplete"):
            self.run_cycle()
        self.assertEqual(self.phase.call_count, 1)

    def test_unknown_edited_stops_without_replay_or_publication(self):
        self.phase.side_effect = [self.original, RuntimeError("edited OutcomeUnknown")]
        with self.assertRaisesRegex(RuntimeError, "OutcomeUnknown"):
            self.run_cycle()
        self.assertEqual(self.phase.call_count, 2)
        self.assertEqual(self.publish.call_count, 1)

    def test_incomplete_second_custody_is_not_cycle_pass(self):
        self.publish.side_effect = [None, ValueError("second custody incomplete")]
        with self.assertRaisesRegex(ValueError, "second custody"):
            self.run_cycle()
        self.assertEqual(self.phase.call_count, 2)

    def test_second_scope_preflight_failure_occurs_before_any_effect(self):
        self.validate.side_effect = [({}, self.context), ValueError("existing ciphertext")]
        with self.assertRaisesRegex(ValueError, "existing ciphertext"):
            self.run_cycle()
        self.phase.assert_not_called()
        self.publish.assert_not_called()

    def test_context_change_is_rejected_before_any_effect(self):
        self.validate.side_effect = [
            ({}, self.context), ({}, {**self.context, "source_sha": "d" * 40}),
        ]
        with self.assertRaisesRegex(ValueError, "context_changed"):
            self.run_cycle()
        self.phase.assert_not_called()

    def test_duplicate_private_destination_is_rejected_before_effect(self):
        self.args.sealed_edited = self.args.sealed_originals
        with self.assertRaisesRegex(ValueError, "distinct"):
            self.run_cycle()
        self.phase.assert_not_called()
        self.validate.assert_not_called()

    def test_nested_private_destination_is_rejected_before_effect(self):
        self.args.edited_evidence_root = self.args.evidence_root / "edited"
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.run_cycle()
        self.phase.assert_not_called()

    def test_missing_second_destination_is_rejected_before_effect(self):
        self.args.sealed_edited = None
        with self.assertRaisesRegex(ValueError, "paths_required"):
            self.run_cycle()
        self.phase.assert_not_called()

    def test_changed_durable_cycle_observation_is_not_pass(self):
        self.observed.return_value = {"source_cycle_observation": "CHANGED"}
        with self.assertRaisesRegex(ValueError, "durable_observation_changed"):
            self.run_cycle()

    def test_missing_final_readback_is_not_cycle_pass(self):
        self.edited["cycle_observation"]["source_cycle_observation"] = "INCOMPLETE"
        with self.assertRaisesRegex(ValueError, "observation_missing"):
            self.run_cycle()
        self.assertEqual(self.phase.call_count, 2)


class OriginalMediaNegativeTests(unittest.TestCase):
    """Synthetic orchestration regressions; these are never native media evidence."""
    def setUp(self):
        import native_manim_camera_evidence as evidence
        import native_manim_camera_pixels as pixels
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.saved = self.stack.enter_context(mock.patch.object(evidence, "private_json"))
        self.stack.enter_context(mock.patch.object(evidence, "read", return_value=b"native"))
        self.stack.enter_context(mock.patch.multiple(
            pixels, FRAME_BYTES=3, FRAMES=3, READBACK_SAMPLES=(0, 1, 2),
        ))
        self.plan = self.stack.enter_context(mock.patch.object(
            pixels, "verified_plan", return_value={"admitted": "edited1040"},
        ))
        self.stack.enter_context(mock.patch.object(pixels, "checked_input"))
        self.digest = self.stack.enter_context(mock.patch.object(
            pixels, "digest", return_value="a" * 64,
        ))
        self.probe = self.stack.enter_context(mock.patch.object(
            pixels, "probe_media", return_value={"frame_count": 3},
        ))
        self.decode = self.stack.enter_context(mock.patch.object(
            pixels, "decoded_rgb120", return_value=b"abcdefghi",
        ))
        self.geometry = self.stack.enter_context(mock.patch.object(
            pixels, "assert_centroids",
            side_effect=AssertionError("native object center diverges from saved camera trajectory"),
        ))
        self.original = {
            "evidence": Path("/evidence/original"),
            "native_path": Path("/evidence/original/native.json"),
            "native": {"result": {"artifact": {"sha256": "a" * 64}}},
        }
        self.prepared = {"plan_path": Path("/evidence/edited/plan.json"), "plan_sha256": "b" * 64}

    def reject_original(self):
        return cycle.reject_original_media(
            self.prepared, self.original, Path("/evidence/edited"), "ffprobe", "ffmpeg",
        )

    def test_only_decoded_geometric_rejection_is_accepted(self):
        result = self.reject_original()
        self.decode.assert_called_once()
        self.geometry.assert_called_once()
        self.assertEqual(self.digest.call_count, 2)
        self.assertEqual(result["status"], "EXPECTED_GEOMETRIC_REJECTION")
        self.assertEqual(result["decoded_frame_count"], 3)
        self.assertEqual(self.plan.call_args.kwargs, {"fixture": "edited1040"})
        self.saved.assert_called_once()

    def test_wrong_media_digest_never_reaches_decode(self):
        self.digest.return_value = "c" * 64
        with self.assertRaisesRegex(ValueError, "media_changed"):
            self.reject_original()
        self.decode.assert_not_called()
        self.geometry.assert_not_called()
        self.saved.assert_not_called()

    def test_probe_failure_is_not_credited_as_geometric_negative(self):
        self.probe.side_effect = AssertionError("wrong physical frame count")
        with self.assertRaisesRegex(AssertionError, "frame count"):
            self.reject_original()
        self.decode.assert_not_called()
        self.saved.assert_not_called()

    def test_wrong_comparator_failure_is_not_credited(self):
        self.geometry.side_effect = AssertionError("native frame is missing the authored color object")
        with self.assertRaisesRegex(ValueError, "before_expected_geometry"):
            self.reject_original()
        self.saved.assert_not_called()

    def test_original_media_matching_edited_geometry_is_rejected(self):
        self.geometry.side_effect = None
        with self.assertRaisesRegex(ValueError, "satisfied_edited_geometry"):
            self.reject_original()
        self.saved.assert_not_called()




class SourceCycleReceiptContractTests(unittest.TestCase):
    """Synthetic JSON at the e9 Rust boundary; no SQLite, native render or effect.

    The envelope, link, inspection and closure fields are taken from
    native-manim-camera-e2e.rs and manim_camera_projection.rs. Program and carrier
    bytes are inert unit data. The capture callback permits source commands only.
    """
    def setUp(self):
        import native_manim_camera_evidence as camera
        self.camera = camera
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(camera, "read", side_effect=self.read))
        self.stack.enter_context(mock.patch.object(camera, "read_object", side_effect=self.read_object))
        self.stack.enter_context(mock.patch.object(camera, "private_json", side_effect=self.write))
        self.reset_fixture()

    @staticmethod
    def encoded(value):
        import json
        return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()

    def read(self, path, *args):
        return self.files[Path(path)]

    def read_object(self, path):
        return self.camera.object_bytes(self.read(path))

    def write(self, path, value):
        path = Path(path)
        if path in self.files:
            raise ValueError("unit exclusive destination already exists")
        self.files[path] = self.encoded(value)

    def closure(self, program):
        plan_raw = bytes(program["trusted_compiler"]["plan_document"])
        source_raw = program["trusted_compiler"]["python_source"].encode()
        entries = {
            "plan.json": plan_raw, "scene.py": source_raw,
            "render-config.json": b'{"unit_fixture_only":true}\n',
            "assets.lock.json": b'{"assets":[],"unit_fixture_only":true}\n',
            "manifest.json": b'{"unit_fixture_only":true,"native_render_verified":false}\n',
        }
        return {
            "plan_schema_version": 4,
            "compiler_contract_id": "motionwright.manim.rust.planar_camera.v1.0",
            "plan_sha256": self.camera.sha(plan_raw),
            "source_sha256": self.camera.sha(source_raw),
            "config_sha256": self.camera.sha(entries["render-config.json"]),
            "assets_lock_sha256": self.camera.sha(entries["assets.lock.json"]),
            "manifest_sha256": self.camera.sha(entries["manifest.json"]),
            "entries": [
                {"path": key, "bytes": len(value), "sha256": self.camera.sha(value)}
                for key, value in sorted(entries.items())
            ],
        }

    def native_document(self, request, binding, closure, carrier_bytes, token, directory):
        source = {
            "relative_path": "mw_" + token + ".mwcam.zip",
            "sha256": self.camera.sha(carrier_bytes), "bytes": len(carrier_bytes),
            "media_type": "application/vnd.motionwright.manim-planar-camera-source+zip",
            "closure": closure,
            "verified_path": str(directory / ("mw_" + token + ".mwcam.zip")),
        }
        def receipt(suffix, command):
            return {
                "request_id": request + suffix, "command": command,
                "project_id": binding["project_id"], "generation": binding["generation"],
                "revision": binding["revision"], "stage": "completed",
                "payload": {"synthetic_unit_receipt": True},
            }
        return {
            "native_manim_camera_dispatch": "PASS", **binding,
            "request_id": request, "receipt_read_error": None, "custody_error": None,
            "original_start_receipt": receipt(
                ":start", "driver.manim-community.camera.render.start"),
            "original_result_receipt": receipt(":result", "driver.manim-community.render.result"),
            "result": {
                "native_driver": "driver:manim-community", "renderer": "manim-community",
                "scene_id": binding["scene_id"], "project_revision": binding["revision"],
                "plan_sha256": closure["plan_sha256"], "job_ref": "manim-" + token,
                "profile": {"width": 640, "height": 360, "frame_rate": 30},
                "artifact": {
                    "camera_source": source, "camera_source_custody_error": None,
                    "relative_path": "videos/mw_" + token + "/360p30/mw_" + token + ".mp4",
                    "sha256": self.camera.sha(b"unit media " + token.encode()),
                    "bytes": 100,
                },
            },
        }

    def link(self, native, path):
        import copy
        return {
            "native_evidence_sha256": self.camera.sha(self.files[path]),
            "request_id": native["request_id"],
            "camera_source": copy.deepcopy(native["result"]["artifact"]["camera_source"]),
            "original_start_receipt": copy.deepcopy(native["original_start_receipt"]),
            "original_result_receipt": copy.deepcopy(native["original_result_receipt"]),
        }

    def retain_native(self, native, path):
        self.files[path] = self.encoded(native)
        self.files[path.parent / "result.json"] = self.encoded({
            "native_evidence_sha256": self.camera.sha(self.files[path]),
        })

    def reset_fixture(self):
        import copy
        from tooling.tests.test_native_manim_camera_evidence import fixture_seed, fixture_source_cycle
        original_seed = fixture_seed()
        self.edit = fixture_source_cycle()
        original_program = original_seed["prepared_program"]
        program = self.edit["prepared_program"]
        program["trusted_compiler"]["python_source"] = "# inert edited unit source, never executed\n"
        program["trusted_compiler"]["source_sha256"] = self.camera.sha(
            program["trusted_compiler"]["python_source"].encode())
        original_binding = copy.deepcopy(original_program["prepared"]["source"]["binding"])
        binding = copy.deepcopy(program["prepared"]["source"]["binding"])
        original_closure, edited_closure = self.closure(original_program), self.closure(program)
        self.files = {}
        self.database = Path("/unit/camera/owner/motionwright-data/motionwright.sqlite3")
        self.evidence = Path("/unit/camera/evidence/edited")
        original_evidence = Path("/unit/camera/evidence/original")
        original_path = original_evidence / self.camera.EVIDENCE_NAME
        self.native_path = self.evidence / self.camera.EVIDENCE_NAME
        self.output_root = Path("/unit/camera/owner/edited1040/output")
        native_original = self.native_document(
            "camera-unit-original", original_binding, original_closure,
            b"unit original source carrier", "a" * 32, Path("/unit/camera/owner/output"))
        self.native = self.native_document(
            "camera-unit-edited1040", binding, edited_closure,
            b"unit current rebound native carrier", "b" * 32, self.output_root)
        self.retain_native(native_original, original_path)
        self.retain_native(self.native, self.native_path)
        self.original = {
            "database": self.database, "evidence": original_evidence,
            "native_path": original_path, "native": native_original,
            "output_root": Path("/unit/camera/owner/output"),
            "current": {"run_id": "123", "source_sha": "1" * 40, "semwright_sha": "2" * 40},
            "prepared": {
                "binding": original_binding, "plan": original_program["prepared"]["source"],
                "plan_sha256": original_closure["plan_sha256"],
                "program_sha256": original_closure["source_sha256"],
            },
        }
        self.prepared = {
            "binding": binding, "plan": copy.deepcopy(program["prepared"]["source"]),
            "plan_sha256": edited_closure["plan_sha256"],
            "program_sha256": edited_closure["source_sha256"],
        }
        original_asset = "00000005-1111-4111-8111-000000000005"
        edited_asset = "00000006-1111-4111-8111-000000000006"
        original_stamp = {
            "resource": "project:" + original_binding["project_id"],
            "generation": original_binding["generation"], "revision": original_binding["revision"],
        }
        attached_stamp = {**original_stamp, "revision": binding["revision"] - 1}
        historical_edited_sha = self.camera.sha(b"unit edited source before revision rebound")
        inspection = self.edit["current_source_inspection"]
        inspection.update({
            "source_asset_id": edited_asset, "source_sha256": historical_edited_sha,
            "origin_binding": copy.deepcopy(original_binding),
            "original_closure": {**copy.deepcopy(edited_closure), "plan_sha256": "c" * 64},
            "current_closure": copy.deepcopy(edited_closure),
            "revision_only_rebound": True,
            "editable_parameters": ["reframe.end_center_x"],
        })
        self.edit.update({
            "original_stamp": original_stamp, "attached_stamp": attached_stamp,
            "original_native": self.link(native_original, original_path),
            "original_source_asset_id": original_asset, "edited_source_asset_id": edited_asset,
            "original_source_sha256": native_original["result"]["artifact"]["camera_source"]["sha256"],
            "edited_source_sha256": historical_edited_sha,
            "current_export_bundle_sha256": self.native["result"]["artifact"]["camera_source"]["sha256"],
            "original_native_receipts_unchanged": True,
            "production_receipt_window_unchanged": True, "production_receipt_window_limit": 256,
        })
        self.edit["preview"] = {
            "expected": attached_stamp,
            **{key: self.edit[key] for key in (
                "original_source_asset_id", "edited_source_asset_id",
                "original_source_sha256", "edited_source_sha256",
            )},
            "current_plan_sha256": "d" * 64, "proposed_plan_sha256": edited_closure["plan_sha256"],
            "change": {
                "type": "upsert_camera_reframe",
                "reframe": copy.deepcopy(program["prepared"]["source"]["reframe"]),
            },
            "native_execution_performed": False, "imported_source_authority_used": False,
        }
        self.edit_path = self.evidence / "source-cycle-edit.json"
        self.readback = {
            "schema": "motionwright.manim.camera.source-cycle/1",
            "phase": "source-cycle-readback",
            **{key: binding[key] for key in ("project_id", "scene_id", "deliverable_id")},
            "canonical_stamp": copy.deepcopy(self.edit["canonical_stamp"]),
            "edit_receipt_sha256": self.camera.sha(self.encoded(self.edit)),
            "edited_native": self.link(self.native, self.native_path),
            "current_source_inspection": copy.deepcopy(inspection),
            **{key: self.edit[key] for key in (
                "source_project_sha256", "original_source_asset_id", "edited_source_asset_id",
                "original_source_sha256", "edited_source_sha256", "current_export_bundle_sha256",
            )},
            "saved_export_matches_native_carrier": True, "source_changed_by_example": False,
            "fresh_sqlite_readback": True, "original_native_receipts_unchanged": True,
            "production_receipt_window_unchanged": True, "production_receipt_window_limit": 256,
            **{key: False for key in (
                "native_execution_performed", "native_compiler_registered",
                "provider_dispatch_performed", "new_render_job_created",
                "imported_source_authority_used", "fresh_registered_source_reopen_verified",
                "native_video_frame_content_verified", "human_acceptance",
            )},
        }
        self.files[self.evidence / "original-media-edited-geometry-negative.json"] = self.encoded({
            "status": "EXPECTED_GEOMETRIC_REJECTION",
            "edited_plan_sha256": self.prepared["plan_sha256"],
            "original_media_sha256": native_original["result"]["artifact"]["sha256"],
        })
        self.capture = mock.Mock(side_effect=self.source_capture)

    def source_capture(self, argv, **kwargs):
        import copy
        self.assertIn(argv[1], ("source-cycle-edit", "source-cycle-readback"))
        self.assertNotIn("realize-camera", argv)
        self.assertEqual(argv[2], str(self.database))
        value = self.edit if argv[1] == "source-cycle-edit" else self.readback
        self.write(Path(argv[7]), value)
        return copy.deepcopy(value)

    def call_edit(self):
        return cycle.prepare_edited_seed(
            self.original, self.evidence, self.database, {},
            source_binary=Path("/unit/native-manim-camera-e2e"), capture=self.capture,
        )

    def call_readback(self):
        self.files[self.edit_path] = self.encoded(self.edit)
        return cycle.verify_edited_source_readback(
            self.original, self.prepared, self.native, self.evidence, self.database,
            self.output_root, {}, source_binary=Path("/unit/native-manim-camera-e2e"),
            capture=self.capture,
        )

    def assert_no_cycle_claim(self):
        self.assertNotIn(self.evidence / "source-cycle-observation.json", self.files)
        for call in self.capture.call_args_list:
            self.assertIn(call.args[0][1], ("source-cycle-edit", "source-cycle-readback"))

    def test_typed_edit_uses_same_database_and_original_native_link(self):
        value = self.call_edit()
        self.assertEqual(value, self.edit)
        self.capture.assert_called_once()
        argv = self.capture.call_args.args[0]
        self.assertEqual(argv[1], "source-cycle-edit")
        self.assertEqual(argv[8], str(self.original["native_path"]))
        self.assertEqual(argv[9], str(self.original["output_root"]))
        self.assertFalse(value["provider_dispatch_performed"])
        self.assert_no_cycle_claim()

    def test_changed_database_is_rejected_before_source_process(self):
        with self.assertRaisesRegex(ValueError, "database_changed"):
            cycle.prepare_edited_seed(
                self.original, self.evidence, self.database.with_name("foreign.sqlite3"), {},
                source_binary=Path("/unit/source"), capture=self.capture,
            )
        self.capture.assert_not_called()
        self.assert_no_cycle_claim()

    def test_edit_stdout_must_equal_durable_rust_receipt(self):
        def changed_stdout(argv, **kwargs):
            value = self.source_capture(argv, **kwargs)
            value["canonical_stamp"]["revision"] += 1
            return value
        self.capture.side_effect = changed_stdout
        with self.assertRaisesRegex(ValueError, "stdout_differs"):
            self.call_edit()
        self.capture.assert_called_once()
        self.assert_no_cycle_claim()

    def test_edit_phase_and_full_original_stamp_cannot_change(self):
        variants = (
            ("phase", lambda v: v.update(phase="source-cycle-readback")),
            ("resource", lambda v: v["original_stamp"].update(resource="project:foreign")),
            ("generation", lambda v: v["original_stamp"].update(generation="foreign")),
            ("revision", lambda v: v["original_stamp"].update(revision=999)),
            ("attached_generation", lambda v: v["attached_stamp"].update(generation="foreign")),
            ("attached_revision", lambda v: v["attached_stamp"].update(revision=True)),
        )
        for label, alter in variants:
            with self.subTest(field=label):
                self.reset_fixture()
                alter(self.edit)
                with self.assertRaises(ValueError):
                    self.call_edit()
                self.capture.assert_called_once()
                self.assert_no_cycle_claim()

    def test_edit_cannot_substitute_original_request_receipt_or_carrier(self):
        variants = (
            ("request", lambda v: v["original_native"].update(request_id="foreign-request")),
            ("receipt", lambda v: v["original_native"]["original_result_receipt"]["payload"].update(changed=True)),
            ("native_hash", lambda v: v["original_native"].update(native_evidence_sha256="0" * 64)),
            ("source_hash", lambda v: v["original_native"]["camera_source"].update(sha256="0" * 64)),
        )
        for label, alter in variants:
            with self.subTest(field=label):
                self.reset_fixture()
                alter(self.edit)
                with self.assertRaises(ValueError):
                    self.call_edit()
                self.assert_no_cycle_claim()

    def test_edit_requires_distinct_assets_and_current_cas_inspection(self):
        variants = (
            ("same_asset", lambda v: v.update(edited_source_asset_id=v["original_source_asset_id"])),
            ("invalid_asset", lambda v: v.update(edited_source_asset_id="not-a-uuid")),
            ("original_hash", lambda v: v.update(original_source_sha256="0" * 64)),
            ("cas_hash", lambda v: v["current_source_inspection"].update(source_sha256="0" * 64)),
            ("preview_asset", lambda v: v["preview"].update(edited_source_asset_id=v["original_source_asset_id"])),
            ("origin_binding", lambda v: v["current_source_inspection"]["origin_binding"].update(revision=999)),
        )
        for label, alter in variants:
            with self.subTest(field=label):
                self.reset_fixture()
                alter(self.edit)
                with self.assertRaises(ValueError):
                    self.call_edit()
                self.assert_no_cycle_claim()

    def test_edit_source_only_and_preservation_flags_are_strict_booleans(self):
        variants = (
            ("provider_dispatch_performed", True),
            ("native_execution_performed", 0),
            ("import_staging_files_removed", False),
            ("original_native_receipts_unchanged", 1),
            ("production_receipt_window_limit", 255),
        )
        for key, value in variants:
            with self.subTest(field=key):
                self.reset_fixture()
                self.edit[key] = value
                with self.assertRaises(ValueError):
                    self.call_edit()
                self.assert_no_cycle_claim()

    def test_fresh_readback_accepts_rebound_carrier_not_historical_cas_sha(self):
        self.assertNotEqual(
            self.edit["edited_source_sha256"], self.edit["current_export_bundle_sha256"])
        value = self.call_readback()
        self.capture.assert_called_once()
        self.assertEqual(self.capture.call_args.args[0][1], "source-cycle-readback")
        self.assertEqual(value["source_cycle_observation"], "PASS_REQUIRES_SEALED_CUSTODY")
        self.assertTrue(value["current_export_equals_second_native_carrier"])
        self.assertFalse(value["source_export_grants_native_authority"])
        self.assertFalse(value["human_acceptance"])
        self.assertEqual(value, self.read_object(self.evidence / "source-cycle-observation.json"))

    def test_readback_foreign_database_is_rejected_before_process(self):
        with self.assertRaisesRegex(ValueError, "database_changed"):
            cycle.verify_edited_source_readback(
                self.original, self.prepared, self.native, self.evidence,
                self.database.with_name("foreign.sqlite3"), self.output_root, {},
                source_binary=Path("/unit/source"), capture=self.capture,
            )
        self.capture.assert_not_called()
        self.assert_no_cycle_claim()

    def test_readback_stdout_must_equal_its_new_durable_receipt(self):
        def changed_stdout(argv, **kwargs):
            value = self.source_capture(argv, **kwargs)
            value["saved_export_matches_native_carrier"] = False
            return value
        self.capture.side_effect = changed_stdout
        with self.assertRaisesRegex(ValueError, "stdout_differs"):
            self.call_readback()
        self.assert_no_cycle_claim()

    def test_readback_rejects_changed_phase_full_stamp_and_project_binding(self):
        variants = (
            ("phase", lambda v: v.update(phase="source-cycle-edit")),
            ("resource", lambda v: v["canonical_stamp"].update(resource="project:foreign")),
            ("generation", lambda v: v["canonical_stamp"].update(generation="foreign")),
            ("revision", lambda v: v["canonical_stamp"].update(revision=999)),
            ("project", lambda v: v.update(project_id="foreign")),
            ("scene", lambda v: v.update(scene_id="foreign")),
        )
        for label, alter in variants:
            with self.subTest(field=label):
                self.reset_fixture()
                alter(self.readback)
                with self.assertRaises(ValueError):
                    self.call_readback()
                self.assert_no_cycle_claim()

    def test_readback_rejects_changed_asset_history_export_or_edit_receipt(self):
        keys = (
            "original_source_asset_id", "edited_source_asset_id",
            "original_source_sha256", "edited_source_sha256",
            "current_export_bundle_sha256", "source_project_sha256", "edit_receipt_sha256",
        )
        for key in keys:
            with self.subTest(field=key):
                self.reset_fixture()
                self.readback[key] = "0" * 64
                with self.assertRaises(ValueError):
                    self.call_readback()
                self.assert_no_cycle_claim()

    def test_readback_rejects_changed_native_request_receipt_and_carrier(self):
        variants = (
            ("request", lambda v: v["edited_native"].update(request_id="foreign")),
            ("receipt", lambda v: v["edited_native"]["original_start_receipt"]["payload"].update(changed=True)),
            ("native_hash", lambda v: v["edited_native"].update(native_evidence_sha256="0" * 64)),
            ("carrier", lambda v: v["edited_native"]["camera_source"].update(sha256="0" * 64)),
        )
        for label, alter in variants:
            with self.subTest(field=label):
                self.reset_fixture()
                alter(self.readback)
                with self.assertRaises(ValueError):
                    self.call_readback()
                self.assert_no_cycle_claim()

    def test_coordinated_native_carrier_change_still_must_match_saved_export(self):
        self.native["result"]["artifact"]["camera_source"]["sha256"] = "0" * 64
        self.retain_native(self.native, self.native_path)
        self.readback["edited_native"] = self.link(self.native, self.native_path)
        with self.assertRaisesRegex(ValueError, "carrier_differs_from_source"):
            self.call_readback()
        self.assert_no_cycle_claim()

    def test_reused_original_native_request_is_not_second_render(self):
        self.native["request_id"] = self.original["native"]["request_id"]
        self.retain_native(self.native, self.native_path)
        self.readback["edited_native"] = self.link(self.native, self.native_path)
        with self.assertRaisesRegex(ValueError, "native_request_reused"):
            self.call_readback()
        self.assert_no_cycle_claim()

    def test_readback_current_plan_closure_and_inspection_cannot_drift(self):
        variants = (
            ("plan", lambda v: v["current_source_inspection"]["current_plan"]["camera"].update(center_x=1000.0)),
            ("binding", lambda v: v["current_source_inspection"]["current_binding"].update(revision=999)),
            ("closure", lambda v: v["current_source_inspection"]["current_closure"].update(source_sha256="0" * 64)),
            ("asset", lambda v: v["current_source_inspection"].update(source_asset_id=v["original_source_asset_id"])),
        )
        for label, alter in variants:
            with self.subTest(field=label):
                self.reset_fixture()
                alter(self.readback)
                with self.assertRaises(ValueError):
                    self.call_readback()
                self.assert_no_cycle_claim()

    def test_readback_cannot_promote_source_process_into_native_authority(self):
        variants = (
            ("fresh_sqlite_readback", 1),
            ("saved_export_matches_native_carrier", False),
            ("original_native_receipts_unchanged", False),
            ("production_receipt_window_limit", 255),
            ("source_changed_by_example", True),
            ("native_execution_performed", True),
            ("provider_dispatch_performed", True),
            ("new_render_job_created", True),
            ("human_acceptance", True),
        )
        for key, value in variants:
            with self.subTest(field=key):
                self.reset_fixture()
                self.readback[key] = value
                with self.assertRaises(ValueError):
                    self.call_readback()
                self.assert_no_cycle_claim()

    def test_uncertain_source_process_is_never_retried_or_published(self):
        self.capture.side_effect = RuntimeError("source process outcome uncertain")
        with self.assertRaisesRegex(RuntimeError, "outcome uncertain"):
            self.call_edit()
        self.capture.assert_called_once()
        self.assert_no_cycle_claim()


if __name__ == "__main__":
    unittest.main()
