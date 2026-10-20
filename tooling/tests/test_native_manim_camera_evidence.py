"""Offline negative controls for camera custody; no native media is produced.

Fixture JSON/MP4 bytes and callback results below are synthetic unit controls.
The real 120-frame decoder is a separate immutable oracle and workflow gate.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_manim_camera_evidence as camera
import native_manim_e2e as wrapper
import native_scene_public_artifact as publisher
from tooling.tests.test_native_manim_camera_pixels import source as pixel_source

SOURCE = "1" * 40
PIN = "2" * 40
RECIPIENT = {"spki_der_base64": "unit-test-only", "sha256": "3" * 64}
CONTEXT = {"source_sha": SOURCE, "semwright_sha": PIN, "run_id": "12345", "run_attempt": "1"}
IDS = {key: f"{n:08d}-1111-4111-8111-{n:012d}"
       for n, key in enumerate(("project_id", "generation", "scene_id", "deliverable_id"), 1)}


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def fixture_seed():
    plan = pixel_source()
    plan["binding"] = {**IDS, "revision": 7}
    raw = encoded(plan)
    program = "# unit control, never executed\n"
    return {
        "schema": "motionwright.native.manim-camera-seed/1",
        **plan["binding"], "source_project_sha256": "4" * 64,
        "fresh_sqlite_readback": True, "native_dispatch_performed": False,
        "decoded_media_verified": False, "frame_count": 120,
        "frame_rate_num": 30, "frame_rate_den": 1,
        "prepared_program": {
            "prepared": {
                "source": plan, "native_execution_performed": False,
                "permissions_verified": False, "imported_source_authority_used": False,
                "native_source_compiler_registered": False,
            },
            "trusted_compiler": {
                "compiler_id": "motionwright.manim.rust.planar_camera.v1.0",
                "plan_document": list(raw), "plan_sha256": camera.sha(raw),
                "python_source": program, "source_sha256": camera.sha(program.encode()),
                "native_execution_performed": False, "runtime_sample_pts_verified": False,
            },
            "original_source_snapshot_checked": True,
            "program_semantically_bound_to_source": True,
            "native_execution_performed": False, "native_permissions_verified": False,
            "native_source_compiler_registered": False,
        },
    }


class FakeCustody:
    """A local failpoint engine; never a cryptographic or native success claim."""
    def __init__(self):
        self.complete = True
        self.seal_error = None
        self.write_failure = None
        self.seal_calls = 0
        self.snapshots = []

    def public_key(self, recipient):
        if recipient != RECIPIENT:
            raise ValueError("fixture recipient changed")

    def validate_context(self, context):
        if set(context) != set(CONTEXT):
            raise ValueError("fixture context changed")

    def new_bytes(self, path, data):
        if self.write_failure == path.name:
            raise OSError("injected custody failure")
        with path.open("xb") as stream:
            stream.write(data)

    def new_json(self, path, value):
        self.new_bytes(path, encoded(value))

    def gather_snapshot(self, roots, *, preserve_partial):
        assert preserve_partial
        self.snapshots.append({k: list(p.rglob("*")) for k, p in roots.items()})
        return b"synthetic capture control only", self.complete

    def encrypt_snapshot(self, data, recipient, context):
        self.seal_calls += 1
        if self.seal_error:
            raise self.seal_error
        return b"MWCISEAL1\nsynthetic encrypted-envelope control only"


class CameraOriginalCustodyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.owner = self.root / "owner"
        self.evidence = self.root / "evidence"
        self.sealed = self.root / "sealed"
        self.output = self.owner / "output"
        self.work = self.owner / "work"
        for path in (self.owner, self.evidence, self.sealed, self.output, self.work):
            path.mkdir(mode=0o700)
        self.engine = FakeCustody()
        self.module = mock.patch.object(camera, "custody_module", return_value=self.engine)
        self.module.start()

    def tearDown(self):
        self.module.stop()
        self.tmp.cleanup()

    def session(self, context=None):
        return camera.CameraCustody(
            self.owner, self.evidence, self.sealed / "originals.mwci",
            RECIPIENT, context or CONTEXT, engine=self.engine,
        )

    def fixture_render(self):
        seed = fixture_seed()
        prepared = camera.prepare_seed(seed, self.evidence)
        token = "a" * 32
        relative = f"videos/mw_{token}/360p30/mw_{token}.mp4"
        media = b"\x00\x00\x00\x18ftypunit-fixture-not-a-native-video"
        getters = encoded({"plan_sha256": prepared["plan_sha256"],
                           "binding": prepared["binding"], "sample_count": 120})
        artifact = {"relative_path": relative, "sha256": camera.sha(media),
                    "bytes": len(media), "camera_getters_sha256": camera.sha(getters)}
        native = {
            "native_manim_camera_dispatch": "PASS", **prepared["binding"],
            "receipt_read_error": None, "request_id": "unit-request",
            "result": {
                "native_driver": "driver:manim-community", "renderer": "manim-community",
                "scene_id": IDS["scene_id"], "project_revision": 7,
                "plan_sha256": prepared["plan_sha256"],
                "profile": {"width": 640, "height": 360, "frame_rate": 30},
                "job_ref": "manim-" + token, "artifact": artifact,
            },
        }
        for name, suffix, command in (
            ("original_start_receipt", ":start", "driver.manim-community.camera.render.start"),
            ("original_result_receipt", ":result", "driver.manim-community.render.result"),
        ):
            native[name] = {"request_id": "unit-request" + suffix, "command": command,
                            "stage": "completed", "project_id": IDS["project_id"],
                            "generation": IDS["generation"], "revision": 7}
        path = self.output / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(media)
        (self.work / f"mw_{token}.py").write_bytes(
            seed["prepared_program"]["trusted_compiler"]["python_source"].encode())
        (self.work / f"mw_{token}.camera.json").write_bytes(getters)
        (self.evidence / camera.EVIDENCE_NAME).write_bytes(encoded(native))
        decoded = {
            "schema": "motionwright.manim.camera.decoded-readback/1",
            "compiled_plan_sha256": prepared["plan_sha256"],
            "trusted_media_sha256": artifact["sha256"], "binding": prepared["binding"],
            "source_bound_video_observation": "PASS", "human_acceptance": False,
            "all_120_decoded_frame_motion": {
                "both_axes_moving_camera_verified_from_decoded_centroids": True,
            },
            "synthetic_unit_callback": True,
        }
        return native, prepared, decoded

    def finish(self, native, prepared):
        return camera.finish_render(native, prepared, self.evidence, self.output, self.work,
                                    SOURCE, PIN, "ffprobe-not-started", "ffmpeg-not-started")

    def complete_fixture(self):
        native, prepared, decoded = self.fixture_render()
        with mock.patch.object(camera, "verify_pixels", return_value=decoded) as decoder:
            with self.session() as current:
                observed = current.once(lambda: native)
                self.finish(observed, prepared)
            decoder.assert_called_once_with(
                prepared["plan_path"], prepared["plan_sha256"],
                self.evidence / camera.MEDIA_NAME, native["result"]["artifact"]["sha256"],
                "ffprobe-not-started", "ffmpeg-not-started")
        return native, prepared

    def test_plan_bytes_are_retained_without_python_reserialization(self):
        seed = fixture_seed()
        original = bytes(seed["prepared_program"]["trusted_compiler"]["plan_document"])
        prepared = camera.prepare_seed(seed, self.evidence)
        self.assertEqual(prepared["plan_path"].read_bytes(), original)
        self.assertEqual(camera.sha(original), prepared["plan_sha256"])
        self.assertEqual(list(self.owner.rglob("*.sqlite3")), [])

    def test_changed_compiler_plan_bytes_fail_before_native_intent(self):
        seed = fixture_seed()
        seed["prepared_program"]["trusted_compiler"]["plan_document"][10] ^= 1
        with self.assertRaisesRegex(camera.CameraEvidenceError, "plan_digest"):
            camera.prepare_seed(seed, self.evidence)
        self.assertFalse((self.evidence / "native-invocation-intent.json").exists())

    def test_foreign_source_binding_fails_even_with_a_valid_plan_hash(self):
        seed = fixture_seed()
        seed["scene_id"] = IDS["project_id"]
        with self.assertRaisesRegex(camera.CameraEvidenceError, "saved_source"):
            camera.prepare_seed(seed, self.evidence)

    def test_source_export_cannot_self_assert_execution_permissions(self):
        seed = fixture_seed()
        seed["prepared_program"]["native_permissions_verified"] = True
        with self.assertRaisesRegex(camera.CameraEvidenceError, "execution_grant"):
            camera.prepare_seed(seed, self.evidence)

    def test_existing_compiler_file_is_not_replaced(self):
        path = self.evidence / "compiled-camera-plan.json"
        path.write_bytes(b"original unknown bytes")
        with self.assertRaises(FileExistsError):
            camera.prepare_seed(fixture_seed(), self.evidence)
        self.assertEqual(path.read_bytes(), b"original unknown bytes")

    def test_native_result_is_retained_if_decoded_motion_fails(self):
        native, prepared, _ = self.fixture_render()
        error = AssertionError("original Y axis did not move")
        current = self.session()
        with mock.patch.object(camera, "verify_pixels", side_effect=error):
            with self.assertRaises(AssertionError) as caught:
                with current:
                    self.finish(current.once(lambda: native), prepared)
        self.assertIs(caught.exception, error)
        self.assertIs(current.native_result, native)
        self.assertEqual(self.engine.seal_calls, 1)
        self.assertFalse((self.evidence / "result.json").exists())
        original = json.loads((self.evidence / "original-terminal.json").read_bytes())
        self.assertEqual(original["native_result"], native)
        self.assertEqual(original["later_error"]["message"], str(error))

    def test_native_unknown_dominates_sealing_and_stderr_failures(self):
        current = self.session()
        original = subprocess.TimeoutExpired(["existing-native-example"], 420)
        self.engine.seal_error = OSError("cannot seal")
        with mock.patch.object(camera.sys.stderr, "write", side_effect=OSError("stderr closed")):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                with current:
                    current.once(lambda: (_ for _ in ()).throw(original))
        self.assertIs(caught.exception, original)
        self.assertIs(current.native_error, original)
        self.assertIsNone(current.native_result)
        self.assertEqual(self.engine.seal_calls, 1)

    def test_intent_persistence_failure_never_calls_native_callback(self):
        self.engine.write_failure = "native-invocation-intent.json"
        callback = mock.Mock()
        with self.assertRaises(OSError):
            with self.session() as current:
                current.once(callback)
        callback.assert_not_called()

    def test_unknown_invocation_cannot_be_replayed_in_the_session(self):
        current = self.session()
        callback = mock.Mock(side_effect=RuntimeError("UNKNOWN"))
        with self.assertRaises(RuntimeError):
            current.once(callback)
        with self.assertRaisesRegex(camera.CameraEvidenceError, "already_attempted"):
            current.once(callback)
        callback.assert_called_once()

    def test_workflow_attempt_two_is_refused_before_any_intent(self):
        context = {**CONTEXT, "run_attempt": "2"}
        with self.assertRaisesRegex(camera.CameraEvidenceError, "cannot_be_replayed"):
            self.session(context)
        self.assertEqual(list(self.evidence.iterdir()), [])

    def test_partial_original_capture_blocks_pass_but_retains_native_ok(self):
        self.engine.complete = False
        original = {"native_callback_result": "retained"}
        current = self.session()
        with self.assertRaisesRegex(camera.CameraEvidenceError, "custody_incomplete") as caught:
            with current:
                current.once(lambda: original)
        self.assertIs(caught.exception.native_result, original)
        self.assertIs(current.native_result, original)
        receipt = json.loads((self.evidence / "camera-custody.json").read_bytes())
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertFalse(receipt["selected_originals_complete"])

    def test_late_receipt_write_failure_keeps_result_and_ciphertext(self):
        self.engine.write_failure = "camera-custody.json"
        original = {"native_callback_result": "retained"}
        current = self.session()
        with self.assertRaises(camera.CameraEvidenceError):
            with current:
                current.once(lambda: original)
        self.assertIs(current.native_result, original)
        self.assertTrue(current.sealed)
        self.assertTrue((self.sealed / "originals.mwci").exists())

    def test_existing_sealed_output_is_never_overwritten(self):
        path = self.sealed / "originals.mwci"
        path.write_bytes(b"original ciphertext")
        with self.assertRaisesRegex(camera.CameraEvidenceError, "existing_ciphertext"):
            self.session()
        self.assertEqual(path.read_bytes(), b"original ciphertext")

    def test_job_identity_cannot_be_swapped_with_valid_media_hash(self):
        native, prepared, _ = self.fixture_render()
        native["result"]["job_ref"] = "manim-" + "b" * 32
        with self.assertRaisesRegex(camera.CameraEvidenceError, "artifact_identity"):
            camera.checked_native_evidence(native, prepared)

    def test_original_receipts_must_belong_to_the_same_project_and_request(self):
        native, prepared, _ = self.fixture_render()
        for key, value in (("project_id", IDS["scene_id"]), ("request_id", "other:start")):
            bad = copy.deepcopy(native)
            bad["original_start_receipt"][key] = value
            with self.subTest(field=key):
                with self.assertRaisesRegex(camera.CameraEvidenceError, "receipt_binding"):
                    camera.checked_native_evidence(bad, prepared)

    def test_driver_python_must_match_the_original_canonical_compiler_bytes(self):
        native, prepared, _ = self.fixture_render()
        (self.work / ("mw_" + "a" * 32 + ".py")).write_bytes(b"replacement program")
        with mock.patch.object(camera, "verify_pixels") as decoder:
            with self.assertRaisesRegex(camera.CameraEvidenceError, "compiler_bytes_differ"):
                self.finish(native, prepared)
        decoder.assert_not_called()

    def test_original_getters_cannot_be_replaced_before_decode(self):
        native, prepared, _ = self.fixture_render()
        (self.work / ("mw_" + "a" * 32 + ".camera.json")).write_bytes(b"{}")
        with mock.patch.object(camera, "verify_pixels") as decoder:
            with self.assertRaisesRegex(camera.CameraEvidenceError, "getter_digest"):
                self.finish(native, prepared)
        decoder.assert_not_called()

    def test_durable_evidence_and_stdout_must_agree(self):
        native, prepared, _ = self.fixture_render()
        native["unretained_change"] = True
        with self.assertRaisesRegex(camera.CameraEvidenceError, "stdout_and_durable"):
            self.finish(native, prepared)

    def test_public_projection_contains_no_raw_receipts_or_source_program(self):
        self.complete_fixture()
        output = self.root / "public"
        result = publisher.publish(self.evidence, output, SOURCE, PIN, "manim", "success",
                                   "camera120", self.sealed / "originals.mwci")
        self.assertEqual({p.name for p in output.iterdir()},
                         {camera.MEDIA_NAME, "public-evidence.json"})
        public = json.dumps(result)
        for private in ("job_ref", "unit-request", "original_start_receipt",
                        str(self.owner), "python_source", RECIPIENT["spki_der_base64"]):
            self.assertNotIn(private, public)
        self.assertFalse(result["camera"]["native_editable_reopen_verified"])
        self.assertFalse(result["camera"]["human_acceptance"])
        self.assertFalse(result["v01_release_authorized"])

    def test_missing_ciphertext_blocks_camera_publication(self):
        self.complete_fixture()
        (self.sealed / "originals.mwci").unlink()
        with self.assertRaises((camera.CameraEvidenceError, FileNotFoundError)):
            camera.verified_publication(self.evidence, SOURCE, PIN,
                                        self.sealed / "originals.mwci")

    def test_stale_source_cannot_publish_a_valid_old_video(self):
        self.complete_fixture()
        with self.assertRaisesRegex(camera.CameraEvidenceError, "result_scope"):
            camera.verified_publication(self.evidence, "5" * 40, PIN,
                                        self.sealed / "originals.mwci")

    def test_failure_publication_does_not_read_or_promote_raw_native_result(self):
        output = self.root / "failed-public"
        result = publisher.publish(self.root / "absent", output, SOURCE, PIN,
                                   "manim", "failure", "camera120", None)
        self.assertEqual(result["native_scene_ci_evidence"], "NOT_VERIFIED")
        self.assertEqual(result["included_files"], [])
        self.assertFalse(result["camera"]["source_bound_two_axis_camera_motion"])

    def test_exact_decoded_reader_receipt_is_required_after_original_native_ok(self):
        self.complete_fixture()
        path = self.evidence / "decoded-camera-readback.json"
        decoded = json.loads(path.read_bytes())
        decoded["all_120_decoded_frame_motion"][
            "both_axes_moving_camera_verified_from_decoded_centroids"] = False
        path.write_bytes(encoded(decoded))
        summary_path = self.evidence / "result.json"
        summary = json.loads(summary_path.read_bytes())
        summary["decoded_readback_sha256"] = camera.sha(path.read_bytes())
        summary_path.write_bytes(encoded(summary))
        with self.assertRaisesRegex(camera.CameraEvidenceError, "decoded_readback"):
            camera.verified_publication(self.evidence, SOURCE, PIN,
                                        self.sealed / "originals.mwci")

    def test_captured_process_nonzero_keeps_original_streams_and_does_not_retry(self):
        current = self.session()
        def spawn(*args, **kwargs):
            kwargs["stdout"].write(b'{"partial":"original"}')
            kwargs["stderr"].write(b"original native UNKNOWN diagnostic")
            return mock.Mock(pid=123, wait=mock.Mock(return_value=2))
        with mock.patch.object(wrapper.os, "O_NOFOLLOW", 0, create=True):
            with mock.patch.object(wrapper.subprocess, "Popen", side_effect=spawn) as process:
                with self.assertRaises(camera.CameraEvidenceError):
                    with current:
                        current.once(lambda: wrapper.captured_json(
                            ["existing-example"], env={}, evidence=self.evidence,
                            label="native-example", camera_custody=current))
        process.assert_called_once()
        self.assertEqual((self.evidence / "native-example.stderr.log").read_bytes(),
                         b"original native UNKNOWN diagnostic")
        self.assertEqual(current.native_process["process_returncode"], 2)
        self.assertEqual(current.native_process["native_outcome"], "NOT_INTERPRETED")
        self.assertIsNone(current.native_result)

    def test_client_timeout_retains_unknown_before_process_cleanup_and_sealing(self):
        current = self.session()
        original = subprocess.TimeoutExpired(["existing-example"], 420)
        process = mock.Mock(pid=123, wait=mock.Mock(side_effect=[original, -9]))
        with mock.patch.object(wrapper.os, "O_NOFOLLOW", 0, create=True):
            with mock.patch.object(wrapper.subprocess, "Popen", return_value=process) as spawn:
                # Model the admitted Linux signal surface without a native process.
                with (mock.patch.object(wrapper.os, "killpg", create=True) as kill,
                      mock.patch.object(wrapper.signal, "SIGKILL", 9, create=True)):
                    with self.assertRaises(subprocess.TimeoutExpired) as caught:
                        with current:
                            current.once(lambda: wrapper.captured_json(
                                ["existing-example"], env={}, evidence=self.evidence,
                                label="native-example", camera_custody=current))
        self.assertIs(caught.exception, original)
        spawn.assert_called_once()
        kill.assert_called_once()
        self.assertEqual(current.native_process["native_outcome"], "UNKNOWN")
        self.assertEqual(self.engine.seal_calls, 1)

    def test_successful_callback_survives_stream_sync_failure_without_false_pass(self):
        current = self.session()
        value = {"original_native_value": "preserved"}
        def spawn(*args, **kwargs):
            kwargs["stdout"].write(encoded(value))
            return mock.Mock(pid=123, wait=mock.Mock(return_value=0))
        with mock.patch.object(wrapper.os, "O_NOFOLLOW", 0, create=True):
            with mock.patch.object(wrapper.subprocess, "Popen", side_effect=spawn):
                with mock.patch.object(wrapper.os, "fsync", side_effect=OSError("sync failure")):
                    with self.assertRaises(camera.CameraEvidenceError):
                        with current:
                            current.once(lambda: wrapper.captured_json(
                                ["existing-example"], env={}, evidence=self.evidence,
                                label="native-example", camera_custody=current))
        self.assertEqual(current.native_result, value)
        self.assertTrue(current.sealed)
        receipt = json.loads((self.evidence / "camera-custody.json").read_bytes())
        self.assertEqual(receipt["status"], "INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
