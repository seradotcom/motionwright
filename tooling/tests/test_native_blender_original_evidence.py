"""Synthetic protocol/custody regressions; these do not execute Blender."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import blender_original_scene_contract as contract
import ci_private_evidence as shared
import native_blender_original_evidence as evidence
from tooling.tests.test_blender_original_scene_contract import fixture, page, property_get


def complete_fixture():
    plan, raw, inventories, customs, refs = fixture()
    obj = plan["contribution"]["objects"][0]
    obj.update(location=[1.0, 2.0, 0.05], rotation=[0.0, 0.0, 0.0],
               scale=[2.0, 1.0, 0.1], color_rgba=[0.2, 0.4, 0.6, 1.0])
    source = plan["contribution"]
    selected = {"relative_path": "blender-edited.blend", "sha256": "ad" * 32, "bytes": 128}
    edit = {"node_id": obj["node_id"], "translation_x_delta": 0.25}
    request = {
        "project_id": source["project_id"], "scene_id": source["scene_id"],
        "expected": {"resource": "synthetic-project-resource",
                     "generation": source["generation"], "revision": source["revision"]},
        "request_id": "synthetic-original-attempt", "phase": "verify_edited",
        "source": selected, "edit": edit,
    }
    simple = []
    def call(tail, args, data):
        simple.append({"command": "driver.blender." + tail, "args": args, "data": data})
    call("file.open", {"path": selected["relative_path"]},
         {"changed": True, "path": selected["relative_path"], "use_scripts": False})
    for root, pages in inventories.items():
        for item in pages:
            call("semantic.objects", {"root": root, "offset": item["offset"], "limit": 256}, item)
    for ref, pages in customs.items():
        for item in pages:
            call("semantic.custom.list", {"ref": ref, "offset": item["offset"], "limit": 256}, item)
    getters = {}
    def props(ref, values):
        getters[ref] = [property_get(ref, key, value) for key, value in values.items()]
        for item in getters[ref]:
            call("semantic.property.get", {"ref": ref, "property": item["property"]}, item)
    def item(ref, name, rna):
        return {"ref": ref, "name": name, "rna_type": rna, "index": 0}
    def relation(ref, prop, targets):
        data = {**page(targets), "relation": prop, "mutation": "domain_specific"}
        call("semantic.relations", {"ref": ref, "property": prop, "offset": 0, "limit": 256}, data)
    def alias(root, name, suffix):
        original = refs[(root, name)]
        derived = original + "-via-" + suffix
        payload = copy.deepcopy(customs[original][0])
        payload["ref"] = derived
        call("semantic.custom.list", {"ref": derived, "offset": 0, "limit": 256}, payload)
        return derived
    oref, mref = refs[("objects", obj["name"])], refs[("materials", obj["material_name"])]
    props(oref, {"location": [1.25, 2.0, 0.05], "rotation_mode": "XYZ",
                 "rotation_euler": obj["rotation"], "scale": obj["scale"], "hide_render": False})
    props(mref, {"diffuse_color": obj["color_rgba"], "roughness": 0.45,
                 "metallic": 0.0, "use_nodes": True})
    relation(oref, "data", [item("synthetic-mesh", "Mesh", "Mesh")])
    relation("synthetic-mesh", "materials",
             [item(alias("materials", obj["material_name"], "mesh"), obj["material_name"], "Material")])
    camera = plan["camera"]
    camera_props = contract.expected_camera_properties(plan)
    camera_ref = refs[("objects", camera["object_name"])]
    camera_data = refs[("cameras", camera["data_name"])]
    props(camera_ref, camera_props["camera_object"])
    props(camera_data, camera_props["camera_data"])
    relation(camera_ref, "data",
             [item(alias("cameras", camera["data_name"], "object"), camera["data_name"], "Camera")])
    scene_ref = refs[("scenes", plan["native_scene_name"])]
    relation(scene_ref, "camera",
             [item(alias("objects", camera["object_name"], "scene-camera"), camera["object_name"], "Object")])
    relation(scene_ref, "render", [item("synthetic-render-settings", "Render", "RenderSettings")])
    props("synthetic-render-settings", camera_props["render_settings"])
    relation(scene_ref, "objects", [
        item(alias("objects", obj["name"], "scene"), obj["name"], "Object"),
        item(alias("objects", camera["object_name"], "scene"), camera["object_name"], "Object")])
    collection = source["collection_name"]
    relation(refs[("collections", collection)], "objects",
             [item(alias("objects", obj["name"], "collection"), obj["name"], "Object")])
    relation(scene_ref, "collection", [item("synthetic-master", "Scene Collection", "Collection")])
    relation("synthetic-master", "children",
             [item(alias("collections", collection, "master"), collection, "Collection")])
    relation("synthetic-master", "objects",
             [item(alias("objects", camera["object_name"], "master"), camera["object_name"], "Object")])
    active = {"name": plan["native_scene_name"], "camera": camera["object_name"], "object_count": 2}
    call("scene.inspect", {}, active)
    readback = {"semantic_generation": 8, "edited": True, "geometry_content_verified": False,
                "inventories": inventories, "custom_lists": customs,
                "property_getters": getters, "active_scene": active}
    receipts, calls = [], []
    for sequence, value in enumerate(simple, 1):
        request_id = request["request_id"] + ":verify-edited:" + f"{sequence:06}"
        response = {
            "data": value["data"], "provenance_checked": True,
            "execution": {"backend": "synthetic-native-driver-backend", "dry_run": False,
                          "provenance": {
                              "provider": "driver:blender", "source": "driver",
                              "execution_provider": "driver:blender", "untrusted_metadata": False,
                              "descriptor_sha256": "ab" * 32, "provider_generation": 17,
                          }},
        }
        receipt = {
            "id": "synthetic-receipt-" + str(sequence), "project_id": source["project_id"],
            "generation": source["generation"], "revision": source["revision"],
            "request_id": request_id, "command": value["command"], "stage": "completed",
            "request_sha256": "bc" * 32,
            "payload": {"broker_request_id": "synthetic-broker-" + str(sequence), "result": response},
        }
        receipts.append(receipt)
        calls.append({
            "sequence": sequence, "command": value["command"], "args": value["args"],
            "mutation": value["command"] not in evidence.READ_COMMANDS, "request_id": request_id,
            "native_error": None,
            "native_result": {"replayed": False, "receipt": receipt, "result": response},
        })
    result = {
        "schema": "motionwright.blender.original-scene-attempt/1", "phase": "verify_edited",
        "source": selected, "edit": edit, "project_json_sha256": plan["project_json_sha256"],
        "coordinator_outcomes_retained": True, "broker_terminal_custody_requires_completed_receipts": True,
        "geometry_content_verified": False, "visual_acceptance": False,
        "before": readback, "readback": readback, "output": None,
    }
    persisted = {
        "schema": "motionwright.blender.original-scene-phase-evidence/1", "request": request,
        "phase_contract_verified": False, "custody_errors": [], "geometry_content_verified": False,
        "human_acceptance": False, "native_terminal": {"status": "ok", "value": result},
        "coordinator_append_custody": {
            "schema": "motionwright.production.private-receipt-custody/1",
            "durable": False, "mutex_poisoned": False, "reserved_slots": 0,
            "reserved_serialized_bytes": 0, "slot_limit": 8,
            "serialized_byte_limit": 32 * 1024 * 1024,
            "pending_without_terminal": 0, "entries": [],
        },
        "journal": {"schema": "motionwright.blender.original-scene-attempt/1",
                    "phase": "verify-edited", "plan": plan, "geometry_error": None,
                    "geometry_result": None, "calls": calls},
        "custody": {"sqlite_reopened": True, "project_unchanged": True, "project": json.loads(raw),
                    "production": {"complete": True, "receipts": receipts, "watermark": {"count": len(receipts)}}},
    }
    return plan, raw, request, persisted


class PhaseBindingTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.raw, self.request, self.persisted = complete_fixture()

    def check(self, *, before=None, after=None):
        returned = copy.deepcopy(self.persisted)
        returned.pop("coordinator_append_custody", None)
        returned["phase_contract_verified"] = True
        source = self.request["source"]
        return evidence.verify_phase(self.plan, self.raw, self.request, returned, self.persisted,
                                     before or source, after or source, None)

    def test_complete_synthetic_chain_binds_uuid_camera_and_original_receipts(self):
        result = self.check()
        self.assertTrue(result["source_UUIDs_bound"])
        self.assertTrue(result["native_camera_getters_bound"])
        self.assertTrue(result["unpersisted_Broker_terminal_not_claimed"])
        self.assertFalse(result["geometry_content_verified"])
        self.assertFalse(result["rendered_camera_or_human_acceptance"])

    def test_missing_private_append_custody_cannot_pass_current_source_contract(self):
        del self.persisted["coordinator_append_custody"]
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "append_custody_shape"):
            self.check()

    def test_unconfirmed_native_terminal_cannot_replace_completed_receipts(self):
        self.persisted["coordinator_append_custody"]["entries"] = [{
            "persistence": "unconfirmed", "mutation": True,
            "attempted_receipt": {"stage": "completed", "payload": {"result": "synthetic-original"}},
        }]
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "append_custody_unconfirmed"):
            self.check()

    def test_pending_or_reserved_private_custody_blocks_verified_phase(self):
        for key in ("pending_without_terminal", "reserved_slots", "reserved_serialized_bytes"):
            with self.subTest(key=key):
                original = copy.deepcopy(self.persisted["coordinator_append_custody"])
                self.persisted["coordinator_append_custody"][key] = 1
                with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "append_custody_unconfirmed"):
                    self.check()
                self.persisted["coordinator_append_custody"] = original

    def test_private_append_custody_rejects_shape_types_and_false_authority(self):
        cases = [
            ("schema", "other/1"), ("durable", True), ("mutex_poisoned", True),
            ("reserved_slots", False), ("reserved_serialized_bytes", False),
            ("pending_without_terminal", False), ("entries", {}),
            ("slot_limit", True), ("slot_limit", 0),
            ("serialized_byte_limit", True), ("serialized_byte_limit", 0),
            ("unexpected_member", "not_allowed"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.persisted["coordinator_append_custody"])
                self.persisted["coordinator_append_custody"][key] = value
                with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "append_custody"):
                    self.check()
                self.persisted["coordinator_append_custody"] = original

    def test_private_append_custody_must_not_leak_into_returned_cli_value(self):
        returned = copy.deepcopy(self.persisted)
        returned["phase_contract_verified"] = True
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "publication_ack"):
            evidence.verify_phase(
                self.plan, self.raw, self.request, returned, self.persisted,
                self.request["source"], self.request["source"], None,
            )

    def test_private_append_projection_does_not_hide_an_unrelated_publication_change(self):
        returned = copy.deepcopy(self.persisted)
        returned.pop("coordinator_append_custody")
        returned["phase_contract_verified"] = True
        returned["native_terminal"]["value"]["source"]["sha256"] = "ff" * 32
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "publication_ack"):
            evidence.verify_phase(
                self.plan, self.raw, self.request, returned, self.persisted,
                self.request["source"], self.request["source"], None,
            )

    def test_missing_completed_receipt_blocks_success(self):
        receipts = self.persisted["custody"]["production"]["receipts"]
        receipts.pop()
        self.persisted["custody"]["production"]["watermark"]["count"] = len(receipts)
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "original_receipt"):
            self.check()

    def test_incomplete_watermark_cannot_be_treated_as_complete_history(self):
        self.persisted["custody"]["production"]["watermark"]["count"] += 1
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "snapshot_incomplete"):
            self.check()

    def test_prior_context_cached_reply_is_rejected(self):
        self.persisted["journal"]["calls"][0]["native_result"]["replayed"] = True
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "prior_context"):
            self.check()

    def test_provider_change_and_untrusted_metadata_are_rejected(self):
        for key, value in [("provider_generation", 99), ("provider", "driver:other"),
                           ("execution_provider", "driver:other"), ("untrusted_metadata", True)]:
            with self.subTest(key=key):
                saved = copy.deepcopy(self.persisted)
                provenance = self.persisted["journal"]["calls"][-1]["native_result"]["result"]["execution"]["provenance"]
                provenance[key] = value
                with self.assertRaises(evidence.OriginalSceneUnverified):
                    self.check()
                self.persisted = saved

    def test_request_identity_revision_and_boolean_sequence_are_rejected(self):
        for kind in ("scene", "revision", "sequence", "mutation"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "scene":
                    self.request["scene_id"] = "00000000-0000-4000-8000-000000000099"
                elif kind == "revision":
                    self.request["expected"]["revision"] = 6
                elif kind == "sequence":
                    self.persisted["journal"]["calls"][0]["sequence"] = True
                else:
                    self.persisted["journal"]["calls"][0]["mutation"] = False
                with self.assertRaises(evidence.OriginalSceneUnverified):
                    self.check()

    def test_bool_or_unsupported_native_camera_getter_is_rejected(self):
        props = self.persisted["native_terminal"]["value"]["readback"]["property_getters"]
        camera_ref = next(ref for ref, rows in props.items() if any(row["property"] == "ortho_scale" for row in rows))
        row = next(row for row in props[camera_ref] if row["property"] == "ortho_scale")
        row["value"] = True
        with self.assertRaises(contract.SceneContractError):
            self.check()

    def test_relation_name_does_not_replace_derived_uuid_readback(self):
        calls = self.persisted["journal"]["calls"]
        target = next(call for call in calls
                      if call["command"] == "driver.blender.semantic.custom.list"
                      and "-via-mesh" in call["args"]["ref"])
        item = next(item for item in target["native_result"]["result"]["data"]["items"]
                    if item["key"] == "motionwright.scene_id")
        item["value"] = "00000000-0000-4000-8000-000000000099"
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "UUID"):
            self.check()

    def test_source_change_after_open_is_rejected(self):
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "retained_source"):
            self.check(after={**self.request["source"], "sha256": "cd" * 32})

    def test_getter_not_in_original_command_journal_is_rejected(self):
        readback = self.persisted["native_terminal"]["value"]["readback"]
        detached = copy.deepcopy(readback)
        ref = next(iter(detached["property_getters"]))
        detached["property_getters"][ref][0]["value"] = [9.0, 9.0, 9.0]
        self.persisted["native_terminal"]["value"]["readback"] = detached
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "original_calls"):
            self.check()

    def test_original_scene_file_phase_cannot_publish_human_or_mesh_acceptance(self):
        for key in ("geometry_content_verified", "human_acceptance"):
            with self.subTest(key=key):
                self.setUp()
                self.persisted[key] = True
                with self.assertRaises(evidence.OriginalSceneUnverified):
                    self.check()


class CapturedCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.prefix = Path(self.temp.name).resolve() / "phase"
        self.record = {}

    def test_native_json_retained_before_command_record_failure(self):
        value = {"native_terminal": {"status": "ok", "value": "synthetic-only"}}
        def execute(_argv, **kw):
            kw["stdout"].write(json.dumps(value).encode())
            return subprocess.CompletedProcess([], 0)
        with mock.patch.object(evidence.subprocess, "run", side_effect=execute) as run, \
                mock.patch.object(evidence, "new_json", side_effect=OSError("record failure")):
            with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "custody"):
                evidence.captured_json(["synthetic-no-process"], env={}, prefix=self.prefix, record=self.record)
        run.assert_called_once()
        self.assertEqual(self.record["returned_json"], value)
        self.assertEqual(self.record["terminal"], {"status": "PROCESS_EXIT", "returncode": 0})

    def test_nonzero_original_stream_is_preserved_without_invented_sdk_classification(self):
        raw = b'{"code":"ConsentRequired","outcome_known":true,"synthetic":true}\n'
        def execute(_argv, **kw):
            kw["stderr"].write(raw)
            return subprocess.CompletedProcess([], 7)
        with mock.patch.object(evidence.subprocess, "run", side_effect=execute) as run:
            with self.assertRaises(evidence.OriginalSceneUnverified):
                evidence.captured_json(["synthetic-no-process"], env={}, prefix=self.prefix, record=self.record)
        run.assert_called_once()
        self.assertEqual(self.prefix.with_suffix(".stderr").read_bytes(), raw)
        self.assertEqual(self.record["terminal"], {"status": "PROCESS_EXIT", "returncode": 7})
        self.assertNotIn("outcome_known", self.record["terminal"])

    def test_timeout_error_identity_survives_secondary_custody_failure(self):
        original = subprocess.TimeoutExpired(["synthetic-no-process"], 1)
        with mock.patch.object(evidence.subprocess, "run", side_effect=original) as run, \
                mock.patch.object(evidence, "new_json", side_effect=OSError("secondary")):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                evidence.captured_json(["synthetic-no-process"], env={}, prefix=self.prefix, record=self.record)
        self.assertIs(raised.exception, original)
        run.assert_called_once()
        self.assertFalse(self.record["terminal"]["outcome_known"])

    def test_reserved_invocation_cannot_be_replayed(self):
        self.record["invocation_reserved"] = True
        with mock.patch.object(evidence.subprocess, "run") as run:
            with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "already_attempted"):
                evidence.captured_json(["synthetic-no-process"], env={}, prefix=self.prefix, record=self.record)
        run.assert_not_called()

    def test_existing_capture_destination_prevents_any_process(self):
        self.prefix.with_suffix(".stdout").write_bytes(b"retained-original")
        with mock.patch.object(evidence.subprocess, "run") as run:
            with self.assertRaises(FileExistsError):
                evidence.captured_json(["synthetic-no-process"], env={}, prefix=self.prefix, record=self.record)
        run.assert_not_called()
        self.assertEqual(self.prefix.with_suffix(".stdout").read_bytes(), b"retained-original")


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        der = cls.private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        cls.recipient = {"spki_der_base64": base64.b64encode(der).decode(),
                         "sha256": hashlib.sha256(der).hexdigest()}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.owner, self.public = self.root / "owner", self.root / "public"
        self.owner.mkdir(); self.public.mkdir()
        self.context = {"source_sha": "a" * 40, "semwright_sha": "b" * 40,
                        "run_id": "123", "run_attempt": "1"}
        self.custody = evidence.OriginalCustody(self.owner, self.public, self.recipient, self.context)

    def test_native_ok_retained_when_later_verification_fails_no_second_effect(self):
        value = {"synthetic": True, "result": {"saved": "original"}}
        original = ValueError("later verification")
        effect = mock.Mock(return_value=value)
        with self.assertRaises(ValueError) as raised:
            self.custody.once("create_original", effect, mock.Mock(side_effect=original))
        self.assertIs(raised.exception, original)
        self.assertIs(self.custody.record["attempts"]["create_original"]["native_result"], value)
        with self.assertRaises(evidence.OriginalSceneUnverified):
            self.custody.once("create_original", effect, lambda x: x)
        effect.assert_called_once()

    def test_native_error_identity_retained_without_verification_or_retry(self):
        original = OSError("synthetic original transport failure")
        verify = mock.Mock()
        with self.assertRaises(OSError) as raised:
            self.custody.once("edit_original", mock.Mock(side_effect=original), verify)
        self.assertIs(raised.exception, original)
        verify.assert_not_called()
        self.assertEqual(self.custody.record["attempts"]["edit_original"]["native_error"]["message"],
                         str(original))

    def test_seal_reuses_shared_authenticated_format_and_retains_memory(self):
        (self.owner / "original.log").write_bytes(b"synthetic-private-original")
        self.custody.record["original_error"] = {"message": "synthetic uncertain outcome"}
        result = self.custody.seal()
        self.assertEqual(result["status"], "SEALED")
        body, context = shared.decrypt_snapshot((self.public / "private-evidence.mwseal").read_bytes(), self.private_key)
        self.assertEqual(context, self.context)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as archive:
            memory = json.load(archive.extractfile("controller-memory.json"))
            self.assertEqual(memory["original_error"], self.custody.record["original_error"])
            self.assertTrue(any(name.endswith("original.log") for name in archive.getnames()))
        self.assertEqual(self.custody.public_summary(None)["status"], "UNVERIFIED")

    def test_snapshot_failure_seals_available_memory_but_never_verifies(self):
        with mock.patch.object(shared, "gather_snapshot", side_effect=OSError("snapshot failure")):
            result = self.custody.seal()
        self.assertFalse(result["selected_originals_complete"])
        body, _ = shared.decrypt_snapshot((self.public / "private-evidence.mwseal").read_bytes(), self.private_key)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:") as archive:
            memory = json.load(archive.extractfile("controller-memory.json"))
        self.assertEqual(memory["secondary_errors"][0]["stage"], "snapshot")
        self.assertEqual(self.custody.public_summary(None)["status"], "UNVERIFIED")

    def test_record_write_failure_is_secondary_and_sealing_is_single_attempt(self):
        with mock.patch.object(evidence, "new_json", side_effect=OSError("controller record failed")):
            result = self.custody.seal()
        self.assertFalse(result["selected_originals_complete"])
        before = (self.public / "private-evidence.mwseal").read_bytes()
        with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "already_attempted"):
            self.custody.seal()
        self.assertEqual((self.public / "private-evidence.mwseal").read_bytes(), before)

    def test_closed_public_projection_contains_no_original_paths_or_errors(self):
        self.custody.secondary("private-path", OSError(str(self.owner) + "/secret.log"))
        summary = self.custody.public_summary(OSError("original secret"))
        self.assertNotIn(str(self.owner), json.dumps(summary))
        self.assertNotIn("secret", json.dumps(summary))
        self.assertFalse(summary["human_acceptance"])
        self.assertFalse(summary["automatic_native_retry"])


    def test_public_stage_keeps_cipher_and_sanitizes_malformed_private_projection(self):
        (self.owner / "private.log").write_bytes(b"synthetic-private-log")
        self.custody.seal()
        (self.public / "public-evidence.json").write_text(
            json.dumps({"secret_path": str(self.owner), "diagnostic": "private secret"}))
        output = self.root / "safe-stage"
        result = evidence.stage_public(self.public, output, self.context, "failure")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertNotIn(str(self.owner), json.dumps(result))
        self.assertNotIn("private secret", json.dumps(result))
        self.assertEqual((output / "private-evidence.mwseal").read_bytes(),
                         (self.public / "private-evidence.mwseal").read_bytes())
        self.assertEqual({p.name for p in output.iterdir()}, {"public-evidence.json", "private-evidence.mwseal"})

    def test_public_stage_does_not_infer_pass_from_seal_or_successful_job(self):
        self.custody.seal()
        evidence.new_json(self.public / "public-evidence.json", self.custody.public_summary(None))
        result = evidence.stage_public(self.public, self.root / "safe-stage", self.context, "success")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["phases_verified"], 0)
        self.assertFalse(result["human_acceptance"])

    def test_closed_public_schema_rejects_raw_fields_false_source_and_bool_counts(self):
        value = self.custody.public_summary(None)
        for changed in ({**value, "raw_command": "private"},
                        {**value, "source_sha": "c" * 40},
                        {**value, "phases_attempted": True},
                        {**value, "human_acceptance": True}):
            with self.subTest(changed=changed), self.assertRaises(evidence.OriginalSceneUnverified):
                evidence.check_public_summary(changed, self.context)

    def test_public_stage_preserves_partial_seal_as_unverified(self):
        with mock.patch.object(shared, "gather_snapshot", side_effect=OSError("snapshot failure")):
            self.custody.seal()
        evidence.new_json(self.public / "public-evidence.json", self.custody.public_summary(None))
        result = evidence.stage_public(self.public, self.root / "safe-stage", self.context, "success")
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertFalse(result["selected_originals_complete"])
        self.assertIsNotNone(result["sealed_originals"])


class ObserverScopeTests(unittest.TestCase):
    def test_observer_finishes_before_context_cleanup_on_original_error(self):
        events, phase_record = [], {}
        observer = mock.Mock()
        observer.start.side_effect = lambda: events.append("start")
        observer.finish.side_effect = lambda: events.append("finish") or {"complete": False}
        custody = mock.Mock()
        @contextmanager
        def context():
            try:
                yield
            finally:
                events.append("cleanup")
        original = ValueError("synthetic native uncertainty")
        with self.assertRaises(ValueError) as raised:
            with context():
                with evidence.live_observation_scope(observer, phase_record, custody, "edit_original"):
                    events.append("effect")
                    raise original
        self.assertIs(raised.exception, original)
        self.assertEqual(events, ["start", "effect", "finish", "cleanup"])
        self.assertFalse(phase_record["observer"]["complete"])

    def test_failed_finish_is_reserved_once_and_does_not_replace_native_error(self):
        original = ValueError("synthetic original failure")
        observer = mock.Mock()
        observer.finish.side_effect = OSError("secondary observation")
        custody = mock.Mock()
        with self.assertRaises(ValueError) as raised:
            with evidence.live_observation_scope(observer, {}, custody, "edit_original"):
                raise original
        self.assertIs(raised.exception, original)
        observer.finish.assert_called_once()
        custody.secondary.assert_called_once()

    def test_verified_finish_is_not_repeated_on_scope_exit(self):
        observer = mock.Mock()
        observer.finish.return_value = {"complete": True}
        record = {}
        with evidence.live_observation_scope(observer, record, mock.Mock(), "verify_edited") as finish:
            self.assertTrue(finish()["complete"])
            with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "already_attempted"):
                finish()
        observer.finish.assert_called_once()
        self.assertEqual(record["observer"], {"complete": True})


class ProcessIdentityTests(unittest.TestCase):
    def observations(self):
        return [{"complete": True, "first": {
            "pid": 100 + index, "start_ticks": 10 + index,
            "daemon_pid": 200 + index, "daemon_start_ticks": 5 + index,
            "blender_sha256": "ab" * 32, "workspace_device": 1, "workspace_inode": index + 1,
        }} for index in range(3)]

    def test_three_fresh_contexts_do_not_claim_file_or_human_verification(self):
        result = evidence.same_original_chain(self.observations())
        self.assertTrue(result["three_distinct_live_native_contexts"])
        self.assertTrue(result["source_file_custody_checked_separately"])
        self.assertFalse(result["human_acceptance"])

    def test_reused_process_broker_or_workspace_prevents_fresh_reopen_claim(self):
        for fields in (("pid", "start_ticks"), ("daemon_pid", "daemon_start_ticks"),
                       ("workspace_device", "workspace_inode")):
            with self.subTest(fields=fields):
                values = self.observations()
                for field in fields:
                    values[1]["first"][field] = values[0]["first"][field]
                with self.assertRaises(evidence.OriginalSceneUnverified):
                    evidence.same_original_chain(values)

    def test_changed_binary_or_incomplete_observation_is_rejected(self):
        for change in ("binary", "incomplete", "missing"):
            with self.subTest(change=change):
                values = self.observations()
                if change == "binary":
                    values[-1]["first"]["blender_sha256"] = "bc" * 32
                elif change == "incomplete":
                    values[-1]["complete"] = False
                else:
                    values.pop()
                with self.assertRaises(evidence.OriginalSceneUnverified):
                    evidence.same_original_chain(values)

    def test_native_arguments_match_pinned_factory_safe_profile(self):
        argv = ["/runtime/blender", "--background", "--factory-startup", "--disable-autoexec",
                "--offline-mode", "-q", "--log-file", "/scratch/native.log",
                "--python-exit-code", "1", "--python", "/adapter/bridge.py",
                "--", "/scratch/socket", "/workspace", "/scratch/session"]
        raw = b"\0".join(part.encode() for part in argv) + b"\0"
        self.assertEqual(evidence.check_native_argv(raw), (argv, "/workspace"))
        for extra in ("unselected.blend", "--python-expr", "--python-use-system-env"):
            bad = argv[:12] + [extra] + argv[12:]
            with self.assertRaises(evidence.OriginalSceneUnverified):
                evidence.check_native_argv(b"\0".join(part.encode() for part in bad) + b"\0")
        for remove in ("--factory-startup", "--disable-autoexec", "--offline-mode"):
            bad = [part for part in argv if part != remove]
            with self.assertRaises(evidence.OriginalSceneUnverified):
                evidence.check_native_argv(b"\0".join(part.encode() for part in bad) + b"\0")

    def test_ancestry_rejects_reused_original_broker(self):
        def identity(pid, _root):
            return {"pid": pid, "parent_pid": 2, "start_ticks": 101, "comm": "blender"}
        with mock.patch.object(evidence, "process_identity", side_effect=identity):
            with self.assertRaisesRegex(evidence.OriginalSceneUnverified, "PID_was_reused"):
                evidence.ancestry(10, {"pid": 10, "start_ticks": 100})

    def test_snapshot_append_preserves_member_bytes_and_rejects_duplicate_memory(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:") as archive:
            member = tarfile.TarInfo("original.bin")
            body = b"\x00\xffsynthetic original"
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        updated = evidence.append_memory_record(raw.getvalue(), {"synthetic": True})
        with tarfile.open(fileobj=io.BytesIO(updated), mode="r:") as archive:
            self.assertEqual(archive.extractfile("original.bin").read(), body)
        with self.assertRaises(evidence.OriginalSceneUnverified):
            evidence.append_memory_record(updated, {"another": True})

    def test_json_rejects_duplicates_nonfinite_and_nonobjects(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'null'):
            with self.subTest(raw=raw), self.assertRaises(evidence.OriginalSceneUnverified):
                evidence.unique_json(raw)


if __name__ == "__main__":
    unittest.main()
