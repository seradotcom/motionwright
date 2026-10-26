"""Adversarial source controls for public Blender and Manim scene evidence.

Static seals below are structural byte fixtures, not encrypted native evidence.
No key, custodian, native process, decoder, or physical acceptance is created.
"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_scene_public_artifact as pub

SHA = "a" * 40
PIN = "b" * 40
SECRET = "OWNER_PRIVATE_SESSION_SECRET"

def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()

class NativeSceneEvidenceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.base = Path(t.name)
        self.private = self.base / "private"
        self.private.mkdir()
        self.out = self.base / "public"
        self.sealed = self.base / "originals.mwci"
        self.context = {"source_sha": SHA, "semwright_sha": PIN,
                        "run_id": "12345", "run_attempt": "1"}
        self.recipient = "3" * 64
        env = patch.dict(os.environ, {"GITHUB_EVENT_NAME": "workflow_dispatch",
                                     "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1",
                                     "PRIVATE_EVIDENCE_RECIPIENT_SHA256": self.recipient})
        env.start()
        self.addCleanup(env.stop)

    def fixture(self, scene):
        self.scene = scene
        config = pub.SCENES[scene]
        if scene == "blender":
            self.media = b"glTF" + struct.pack("<II", 2, 32) + b"blob" * 5
            asset = {"sha256": hash_bytes(self.media), "bytes": len(self.media),
                     "meshes": 2, "materials": 2}
        else:
            self.media = b"\x00\x00\x00\x18ftyp" + b"isom" + b"\x00" * 16
            asset = {"sha256": hash_bytes(self.media), "bytes": len(self.media),
                     "width": 640, "height": 360,
                     "relative_path": "videos/owned/360p12/owned.mp4"}
        project = {"project_id": SECRET, "generation": SECRET,
                   "revision": 9, "scene_id": SECRET}
        self.result = {f"native_{scene}_e2e": "PASS",
                       "motionwright_sha": SHA, "semwright_sha": PIN,
                       "semwright_version": "1.0.0",
                       "capabilities_checked": sorted(config["commands"]),
                       "project": project, config["asset_key"]: asset}
        self.receipt = {f"native_{scene}_e2e": "PASS", **project,
                        "receipt_count": (10 if scene == "manim"
                                          else config["required_count"]),
                        "completed_commands": sorted(config.get("allowed_completed_commands", config["commands"])),
                        "result": {"native_driver": "driver:blender" if scene == "blender"
                                   else "driver:manim-community",
                                   "artifact": {"sha256": asset["sha256"],
                                                "bytes": asset["bytes"]},
                                   "private_job": SECRET}}
        self.write()
        (self.private / "semwrightd.log").write_text(SECRET)

    def write(self):
        c = pub.SCENES[self.scene]
        if self.scene == "manim":
            self.write_static_custody_fixture()
        (self.private / "result.json").write_text(json.dumps(self.result))
        (self.private / c["evidence"]).write_text(json.dumps(self.receipt))
        (self.private / c["media"]).write_bytes(self.media)

    def write_static_custody_fixture(self):
        # A closed structural envelope deliberately uses synthetic ciphertext.
        # The production reader does not decrypt or authenticate a producer.
        import ci_private_evidence as custody
        self.header = {
            "schema": custody.SEAL_SCHEMA, "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
            "recipient_spki_sha256": self.recipient, "context": self.context,
            "nonce": base64.b64encode(b"n" * 12).decode(),
            "wrapped_key": base64.b64encode(b"k" * 384).decode(),
            "plaintext_sha256": "4" * 64, "plaintext_bytes": 1,
        }
        aad = custody.json_bytes(self.header)
        seal = custody.MAGIC + len(aad).to_bytes(4, "big") + aad + b"x" * 17
        self.sealed.write_bytes(seal)
        self.result.update({"manim_version": "0.21.0", "native_effect_invocations": 1,
                            "sealed_custody_required": True,
                            "native_editable_reopen_verified": False, "human_acceptance": False})
        self.receipt["runtime_observation"] = {"source_unit_fixture_only": True}
        self.receipt.setdefault("request_id", "unit-original-render")
        for name, command, suffix in (
            ("original_start_receipt", "driver.manim-community.editable.render.start", ":start"),
            ("original_result_receipt", "driver.manim-community.render.result", ":result"),
        ):
            self.receipt.setdefault(name, {"command": command, "stage": "completed",
                "request_id": self.receipt["request_id"] + suffix,
                **{key: self.result["project"][key] for key in ("project_id", "generation", "revision")}})
        self.receipt["result"].setdefault("result", {})["receipt"] = self.receipt["original_result_receipt"]
        observed = {"schema": "motionwright-manim-runtime-probe-e2e/1",
                    **{key: self.result["project"][key] for key in ("project_id", "generation", "revision")},
                    "observation": self.receipt["runtime_observation"],
                    "error": None, "render_started": False}
        native_raw = json.dumps(self.receipt).encode()
        observation_raw = json.dumps(observed).encode()
        self.result["native_evidence_sha256"] = hash_bytes(native_raw)
        self.result["runtime_observation_sha256"] = hash_bytes(observation_raw)
        self.result["sealed_originals_sha256"] = hash_bytes(seal)
        self.result["sealed_originals_bytes"] = len(seal)
        pending = {key: value for key, value in self.result.items()
                   if key not in ("sealed_originals_sha256", "sealed_originals_bytes")}
        pending["native_manim_e2e"] = "PASS_REQUIRES_SEALED_CUSTODY"
        sealed = {
            "schema": "motionwright.manim.camera.sealed-custody/1", "status": "PASS",
            "context": self.context, "ciphertext_sha256": hash_bytes(seal),
            "ciphertext_bytes": len(seal), "recipient_sha256": self.recipient,
            "selected_originals_complete": True, "native_result_retained": True,
            "secondary_error_count": 0, "original_owner_tree_removed": False,
            "atomic_sqlite_snapshot_claimed": False, "producer_authenticated_by_encryption": False,
        }
        terminal = {
            "schema": "motionwright.manim.camera.original-terminal/1",
            "native_example_invoked": True, "native_result": self.receipt,
            "native_error": None, "later_error": None, "secondary_errors": [],
            "automatic_retry": False, "source_or_human_acceptance": False,
            "original_process_observation": {"pid": 123, "process_returncode": 0,
                "native_outcome": "NOT_INTERPRETED", "automatic_retry": False},
        }
        report = b'{"source_unit_fixture_only": true}\n'
        source = b'# synthetic unit source, never executed\n'
        values = {
            "static-render-verification.json": pending,
            "camera-custody.json": sealed,
            "original-terminal.json": terminal,
            "native-invocation-intent.json": {"context": self.context,
                "native_example_invocations": 1, "automatic_retry": False},
            "runtime-inventory.json": {"schema": "motionwright.manim.runtime-inventory/2",
                "context": self.context, "manim_version": "0.21.0", "pyav_version": "15.0.0"},
            "native-video-admission.json": {
                "schema": "motionwright.manim.original-native-video-admission/1", "status": "PASS",
                "authority": "original_driver_rust_parser_and_receipt",
                "artifact_sha256": self.result["mp4"]["sha256"],
                "report_sha256": hash_bytes(report), "source_sha256": hash_bytes(source),
                "native_decode_or_render_invocations": 0},
        }
        for name, value in values.items():
            (self.private / name).write_text(json.dumps(value))
        (self.private / "native-example.stdout.log").write_bytes(native_raw)
        (self.private / "motionwright-manim-runtime-observation.json").write_bytes(observation_raw)
        retained = self.private / "native-video-originals"
        retained.mkdir(exist_ok=True)
        (retained / "decoded-video.json").write_bytes(report)
        (retained / "source.py").write_bytes(source)

    def reject(self, code):
        with self.assertRaises(pub.PublicationError) as cm:
            pub.publish(self.private, self.out, SHA, PIN, self.scene, "success",
                        sealed_originals=self.sealed)
        self.assertEqual(cm.exception.code, code)
        self.assertFalse(self.out.exists())

    def test_verified_media_only_no_original_owner_receipts(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            target = self.base / ("published-" + scene)
            result = pub.publish(self.private, target, SHA, PIN, scene, "success",
                                 sealed_originals=self.sealed)
            self.assertEqual({f.name for f in target.iterdir()},
                             {"public-evidence.json", pub.SCENES[scene]["media"]})
            self.assertEqual(result["included_files"][0]["sha256"], hash_bytes(self.media))
            self.assertEqual(result["included_files"][0]["bytes"], len(self.media))
            txt = (target / "public-evidence.json").read_text()
            self.assertNotIn(SECRET, txt)
            self.assertFalse(result["v01_release_authorized"])
            self.assertFalse(result["raw_daemon_or_sandbox_logs_uploaded"])
            self.assertFalse(result["native_effects_replayed_by_publisher"])
            self.assertEqual(result["installed_owner_acceptance"], "NOT_RUN")

    def test_manim_polling_count_varies_without_weakening_original_stages(self):
        # No second render: only original status polling adds journal rows.
        # All source/project/receipt/media bytes stay bound and verified.
        self.fixture("manim")
        for count in (5, 8, 10, 11, 20, 256):
            with self.subTest(count=count):
                self.receipt["receipt_count"] = count
                self.write()
                dest = self.base / ("manim-polling-" + str(count))
                report = pub.publish(self.private, dest, SHA, PIN, "manim", "success",
                                     sealed_originals=self.sealed)
                self.assertEqual(report["included_files"][0]["sha256"],
                                 hash_bytes(self.media))
                self.assertEqual(set(p.name for p in dest.iterdir()),
                                 {"public-evidence.json", pub.SCENES["manim"]["media"]})
                self.assertNotIn(SECRET, (dest / "public-evidence.json").read_text())

    def test_manim_malformed_or_unproven_stages_fail_closed(self):
        self.fixture("manim")
        original = json.loads(json.dumps(self.receipt))
        cases = [
            (True, original["completed_commands"]),
            (0, original["completed_commands"]),
            (4, original["completed_commands"]),
            (257, original["completed_commands"]),
            (10, ["driver.manim-community.editable.render.start",
                  "driver.manim-community.render.status"]),
            (10, ["driver.manim-community.editable.render.start",
                  "driver.manim-community.render.result"]),
            (10, ["driver.manim-community.editable.render.start",
                  "driver.manim-community.render.status",
                  "driver.manim-community.render.result",
                  "driver.manim-community.render.result"]),
            (10, ["driver.manim-community.editable.render.start",
                  "driver.manim-community.render.status",
                  "driver.manim-community.render.result",
                  "driver.other-provider.render.start"]),
            (10, ["driver.manim-community.render.start",
                  "driver.manim-community.render.status",
                  "driver.manim-community.render.result"]),
        ]
        for i, (count, commands) in enumerate(cases):
            with self.subTest(index=i):
                self.receipt = json.loads(json.dumps(original))
                self.receipt["receipt_count"] = count
                self.receipt["completed_commands"] = commands
                self.write()
                self.reject("native_scene_receipts_not_verified")

    def test_failure_never_copies_broker_material(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            for status in ("failure", "cancelled", "skipped"):
                dest = self.base / ("status-" + scene + status)
                x = pub.publish(self.private, dest, SHA, PIN, scene, status)
                self.assertEqual(x["native_scene_ci_evidence"], "NOT_VERIFIED")
                self.assertEqual(x["included_files"], [])
                self.assertEqual([f.name for f in dest.iterdir()], ["public-evidence.json"])

    def test_source_and_provenance_mismatch_rejected(self):
        self.fixture("blender")
        self.result["semwright_sha"] = "c" * 40
        self.write()
        self.reject("native_scene_receipts_not_verified")
        self.result["semwright_sha"] = PIN
        self.receipt["generation"] = "FOREIGN"
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_wrong_digest_or_swapped_media_rejected(self):
        for scene in pub.SCENES:
            self.fixture(scene)
            (self.private / pub.SCENES[scene]["media"]).write_bytes(self.media + b"changed")
            self.reject("native_scene_artifact_integrity_mismatch")

    def test_forged_receipt_digest_refused(self):
        self.fixture("manim")
        self.receipt["result"]["artifact"]["sha256"] = "d" * 64
        self.write()
        self.reject("native_scene_artifact_identity_mismatch")

    def test_missing_native_private_receipts_refused(self):
        self.fixture("blender")
        (self.private / pub.SCENES["blender"]["evidence"]).unlink()
        self.reject("missing_or_linked_public_source")

    def test_duplicate_authority_fields_refused(self):
        self.fixture("blender")
        (self.private / "result.json").write_text(
            '{"motionwright_sha":"first","motionwright_sha":"second"}'
        )
        self.reject("duplicate_evidence_json_key")

    def test_linked_original_native_media_refused(self):
        self.fixture("manim")
        target = self.private / pub.SCENES["manim"]["media"]
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda p: p == target or original(p)):
            self.reject("missing_or_linked_public_source")

    def test_forged_native_success_claim_refused(self):
        self.fixture("blender")
        self.result["native_blender_e2e"] = "FAIL"
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_unsupported_mode_invalid_status_and_overlap_refused(self):
        self.fixture("blender")
        for scene, status in (("other", "success"), ("blender", "pending")):
            with self.assertRaises(pub.PublicationError):
                pub.publish(self.private, self.out, SHA, PIN, scene, status)
        with self.assertRaises(pub.PublicationError):
            pub.publish(self.private, self.private / "out",
                        SHA, PIN, "blender", "failure")

    def test_workflows_upload_only_curated_assets(self):
        root = Path(__file__).resolve().parents[2]
        for scene in pub.SCENES:
            yml = (root / ".github/workflows" / ("native-" + scene + "-e2e.yml")).read_text()
            if scene == "manim":
                lines = yml.splitlines()
                name = "          name: motionwright-native-manim-${{ github.sha }}"
                self.assertEqual(lines.count(name), 1)
                start = lines.index(name) + 1
                self.assertEqual(lines[start:start + 4], [
                    "          path: |",
                    "            verification/native-manim-public/public-evidence.json",
                    "            verification/native-manim-public/motionwright-manim-community.mp4",
                    "          if-no-files-found: error",
                ])
            else:
                self.assertIn("path: verification/native-" + scene + "-public", yml)
            self.assertNotIn("path: verification/native-" + scene + "-e2e\n", yml)
            self.assertIn("test_native_scene_public_artifact.py", yml)

    def static_fixture(self):
        self.fixture("manim")
        media, summary = pub.verified_static_publication(self.private, SHA, PIN, self.sealed)
        self.assertEqual(media, self.media)
        self.assertEqual(summary["native_source_route"], "saved-editable-scene")
        self.assertFalse(summary["native_editable_reopen_verified"])
        self.assertFalse(summary["producer_authenticated_by_encryption"])

    def assert_static_refused(self, pattern=None):
        expectation = (self.assertRaisesRegex((ValueError, OSError), pattern) if pattern
                       else self.assertRaises((ValueError, OSError)))
        with expectation:
            pub.publish(self.private, self.out, SHA, PIN, "manim", "success",
                        sealed_originals=self.sealed)
        self.assertFalse(self.out.exists())

    def changed_static_object(self, name, change, pattern):
        path = self.private / name
        original = path.read_bytes()
        value = json.loads(original)
        change(value)
        path.write_text(json.dumps(value))
        try:
            self.assert_static_refused(pattern)
        finally:
            path.write_bytes(original)

    def test_static_requires_selected_seal_and_refuses_legacy_stage_substitution(self):
        self.static_fixture()
        with self.assertRaisesRegex(pub.PublicationError, "original_static_custody_required"):
            pub.publish(self.private, self.out, SHA, PIN, "manim", "success")
        self.sealed.unlink()
        self.assert_static_refused()
        self.fixture("manim")
        self.receipt["completed_commands"] = ["driver.manim-community." + name
                                             for name in ("render.start", "render.status", "render.result")]
        self.write()
        self.reject("native_scene_receipts_not_verified")

    def test_static_dispatch_run_attempt_and_recipient_cannot_rebind_originals(self):
        self.static_fixture()
        for key, value in (("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_RUN_ATTEMPT", "2"),
                           ("GITHUB_RUN_ID", "999"), ("PRIVATE_EVIDENCE_RECIPIENT_SHA256", "5" * 64)):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                self.assert_static_refused()

    def test_static_pending_record_cannot_be_promoted_or_disagree_after_seal(self):
        self.static_fixture()
        for key, value in (("native_manim_e2e", "PASS"), ("native_effect_invocations", True),
                           ("human_acceptance", True), ("runtime_observation_sha256", "5" * 64)):
            with self.subTest(key=key):
                self.changed_static_object("static-render-verification.json",
                    lambda record: record.__setitem__(key, value), "static_public_preseal_result_binding")
        self.changed_static_object("result.json",
            lambda record: record.__setitem__("native_effect_invocations", True), "static_public_result_scope")

    def test_static_unknown_or_later_terminal_error_never_publishes(self):
        self.static_fixture()
        changes = (
            lambda row: row.__setitem__("native_error", {"outcome_known": False}),
            lambda row: row.__setitem__("later_error", {"type": "CleanupError"}),
            lambda row: row.__setitem__("secondary_errors", [{"stage": "sync"}]),
            lambda row: row.__setitem__("automatic_retry", True),
            lambda row: row.__setitem__("source_or_human_acceptance", True),
            lambda row: row["original_process_observation"].__setitem__("native_outcome", "UNKNOWN"),
            lambda row: row["original_process_observation"].__setitem__("process_returncode", False),
            lambda row: row["original_process_observation"].__setitem__("pid", True),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                self.changed_static_object("original-terminal.json", change, "static_public_original_terminal")

    def test_static_incomplete_custody_or_unearned_claims_never_publish(self):
        self.static_fixture()
        for key, value in (("status", "INCOMPLETE"), ("selected_originals_complete", False),
                           ("native_result_retained", False), ("secondary_error_count", False),
                           ("original_owner_tree_removed", True), ("atomic_sqlite_snapshot_claimed", True),
                           ("producer_authenticated_by_encryption", True)):
            with self.subTest(key=key):
                self.changed_static_object("camera-custody.json", lambda row: row.__setitem__(key, value),
                                           "static_public_custody_incomplete")

    def test_static_ciphertext_and_header_cannot_be_rebound_by_rehashing_receipts(self):
        self.static_fixture()
        self.sealed.write_bytes(self.sealed.read_bytes() + b"changed")
        self.assert_static_refused("static_public_ciphertext_missing_or_changed")
        changes = (
            lambda header: header["context"].__setitem__("run_id", "999"),
            lambda header: header["context"].__setitem__("source_sha", "c" * 40),
            lambda header: header.__setitem__("recipient_spki_sha256", "5" * 64),
            lambda header: header.__setitem__("plaintext_bytes", True),
            lambda header: header.__setitem__("nonce", base64.b64encode(b"n" * 11).decode()),
            lambda header: header.__setitem__("wrapped_key", base64.b64encode(b"k" * 383).decode()),
            lambda header: header.__setitem__("extra", "not-admitted"),
        )
        import ci_private_evidence as custody
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                self.fixture("manim")
                header = copy.deepcopy(self.header)
                change(header)
                aad = custody.json_bytes(header)
                raw = custody.MAGIC + len(aad).to_bytes(4, "big") + aad + b"x" * 17
                self.sealed.write_bytes(raw)
                for name, hash_key, size_key in (
                    ("result.json", "sealed_originals_sha256", "sealed_originals_bytes"),
                    ("camera-custody.json", "ciphertext_sha256", "ciphertext_bytes"),
                ):
                    path = self.private / name
                    record = json.loads(path.read_bytes())
                    record[hash_key], record[size_key] = hash_bytes(raw), len(raw)
                    path.write_text(json.dumps(record))
                self.assert_static_refused("static_public_seal_")

    def test_static_durable_editable_receipts_require_original_command_stamp_and_request(self):
        for key, value in (("command", "driver.manim-community.render.start"),
                           ("stage", "failed"), ("generation", "FOREIGN"),
                           ("revision", 9.0), ("request_id", "foreign:start")):
            with self.subTest(key=key):
                self.static_fixture()
                self.receipt["original_start_receipt"][key] = value
                self.write()
                self.assert_static_refused("static_public_original_editable_receipts")

    def test_static_native5_inventory_report_and_source_are_bound_originals(self):
        self.static_fixture()
        for name in ("native-video-originals/decoded-video.json", "native-video-originals/source.py"):
            with self.subTest(name=name):
                path = self.private / name
                raw = path.read_bytes()
                path.write_bytes(raw + b"changed")
                self.assert_static_refused("static_public_native5_original_admission")
                path.write_bytes(raw)
        self.changed_static_object("runtime-inventory.json",
            lambda row: row["context"].__setitem__("source_sha", "c" * 40),
            "static_public_native5_original_admission")
        for key, value in (("authority", "synthetic_self_claim"), ("native_decode_or_render_invocations", False)):
            self.changed_static_object("native-video-admission.json", lambda row: row.__setitem__(key, value),
                                       "static_public_native5_original_admission")

    def test_static_original_change_during_readback_never_creates_public_output(self):
        self.static_fixture()
        import native_manim_camera_evidence as camera
        read = camera.read
        calls = 0
        def changing_read(path, *args):
            nonlocal calls
            raw = read(path, *args)
            if Path(path).name == "native-example.stdout.log":
                calls += 1
                if calls == 2:
                    return raw + b"changed"
            return raw
        with patch.object(camera, "read", side_effect=changing_read):
            self.assert_static_refused("static_public_originals_changed_during_readback")
        self.assertEqual(calls, 2)

    def test_static_public_reader_never_encrypts_decrypts_or_starts_a_process(self):
        self.static_fixture()
        import ci_private_evidence as custody
        import subprocess
        before = {str(path): path.read_bytes() for path in self.private.rglob("*") if path.is_file()}
        seal = self.sealed.read_bytes()
        with patch.object(custody, "encrypt_snapshot", side_effect=AssertionError("no encryption")), \
             patch.object(custody, "decrypt_snapshot", side_effect=AssertionError("no decryption")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("no process")):
            report = pub.publish(self.private, self.out, SHA, PIN, "manim", "success",
                                 sealed_originals=self.sealed)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.private.rglob("*") if path.is_file()})
        self.assertEqual(self.sealed.read_bytes(), seal)
        self.assertFalse(report["static"]["producer_authenticated_by_encryption"])
        self.assertNotIn(SECRET, (self.out / "public-evidence.json").read_text())

    def test_static_failed_job_never_reads_or_promotes_originals(self):
        for status in ("failure", "cancelled", "skipped"):
            with self.subTest(status=status), \
                 patch.object(pub, "verified_static_publication", side_effect=AssertionError("no read")):
                result = pub.publish(self.private / "missing", self.base / status,
                                     SHA, PIN, "manim", status)
            self.assertEqual(result["native_scene_ci_evidence"], "NOT_VERIFIED")
            self.assertEqual(result["included_files"], [])
            self.assertIsNone(result["static"]["native_effect_invocations"])

if __name__ == "__main__":
    unittest.main()
