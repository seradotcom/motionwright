from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import blender_original_scene_contract as contract


def fixture():
    # Synthetic protocol data only: no native Scene or acceptance is produced.
    project_id = "00000000-0000-4000-8000-000000000001"
    generation = "00000000-0000-4000-8000-000000000002"
    scene_id = "00000000-0000-4000-8000-000000000003"
    node_id = "00000000-0000-4000-8000-000000000004"
    camera_source = {"center_x": 960.0, "center_y": 540.0, "zoom": 1.0,
                     "rotation_deg": 0.0, "safe_margin": 0.05}
    source = {"id": project_id, "generation": generation, "revision": 7,
              "scenes": [{"id": scene_id, "renderer": "blender", "camera": camera_source,
                          "nodes": [{"id": node_id}]}]}
    raw = json.dumps(source, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    plan = {
        "schema": contract.PLAN_SCHEMA, "project_json_sha256": digest,
        "native_scene_name": "mw-native-scene-" + scene_id.replace("-", ""),
        "contribution": {
            "project_id": project_id, "generation": generation, "revision": 7,
            "scene_id": scene_id, "collection_name": "mw-scene-" + scene_id.replace("-", ""),
            "objects": [{"node_id": node_id, "name": "Shape-source-000000000004",
                         "material_name": "Shape-source-000000000004-material"}],
        },
        "camera": {
            "object_name": "mw-camera-" + scene_id.replace("-", ""),
            "data_name": "mw-camera-data-" + scene_id.replace("-", ""),
            "source": copy.deepcopy(camera_source), "location": [0.0, 0.0, 10.01],
            "rotation_euler": [0.0, 0.0, 0.0], "projection": "ORTHO",
            "sensor_fit": "VERTICAL", "ortho_scale": 10.8,
            "clip_start": 0.1, "clip_end": 20.0, "resolution": [1920, 1080],
            "pixel_aspect": [1.0, 1.0],
        },
        "identities": [],
    }
    base = {
        "motionwright.project_id": project_id, "motionwright.generation": generation,
        "motionwright.scene_id": scene_id, "motionwright.revision": "7",
        "motionwright.project_json_sha256": digest,
    }
    rows = [
        ("scenes", plan["native_scene_name"], "native_scene", None),
        ("collections", plan["contribution"]["collection_name"], "scene_collection", None),
        ("objects", plan["camera"]["object_name"], "scene_camera", None),
        ("cameras", plan["camera"]["data_name"], "scene_camera_data", None),
        ("objects", plan["contribution"]["objects"][0]["name"], "canvas_object", node_id),
        ("materials", plan["contribution"]["objects"][0]["material_name"], "canvas_material", node_id),
    ]
    inventories = {root: [page([])] for root in contract.ROOTS}
    customs = {}
    refs = {}
    for index, (root, name, role, node) in enumerate(rows):
        properties = {**base, "motionwright.role": role}
        if node is not None:
            properties["motionwright.node_id"] = node
        plan["identities"].append({"root": root, "name": name, "properties": properties})
        ref = "synthetic-unit-ref-" + str(index)
        refs[(root, name)] = ref
        inventories[root][0]["items"].append({"ref": ref, "name": name, "rna_type": root})
        customs[ref] = [page([{"key": key, "status": "managed", "value": value}
                             for key, value in properties.items()], ref=ref)]
    return plan, raw, inventories, customs, refs


def page(items, *, ref=None, generation=8, offset=0, truncated=False):
    result = {"items": items, "generation": generation, "offset": offset, "truncated": truncated}
    if ref is not None:
        result["ref"] = ref
    return result


def property_get(ref, key, value):
    return {"ref": ref, "property": key, "value": value, "descriptor": {"id": key, "status": "managed"}}


def relation(ref, target_refs, *, property_name="objects", generation=8):
    return [{"command": "driver.blender.semantic.relations",
             "args": {"ref": ref, "property": property_name, "offset": 0},
             "data": {**page([{"ref": target, "name": "unit", "rna_type": "Object", "index": index}
                             for index, target in enumerate(target_refs)], generation=generation),
                      "relation": property_name, "mutation": "domain_specific"}}]


class IdentityReadbackTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.raw, self.inventories, self.customs, self.refs = fixture()

    def check(self):
        return contract.check_identity_readback(self.plan, 8, self.inventories, self.customs)

    def object_ref(self):
        return self.refs[("objects", self.plan["contribution"]["objects"][0]["name"])]

    def test_complete_full_uuid_identity_is_data_only(self):
        contract.check_project_source(self.plan, self.raw)
        result = self.check()
        self.assertEqual(len(result["entities"]), 6)
        self.assertEqual(result["unowned_runtime_entities"], [])
        self.assertNotIn("native_pass", result)
        self.assertNotIn("approval", result)

    def test_unmarked_factory_extras_are_reported_without_source_ownership(self):
        ref = "synthetic-factory-cube"
        self.inventories["objects"][0]["items"].append(
            {"ref": ref, "name": "Cube", "rna_type": "Object"})
        self.customs[ref] = [page([], ref=ref)]
        result = self.check()
        self.assertEqual(result["unowned_runtime_entities"],
                         [{"root": "objects", "name": "Cube", "ref": ref}])

    def test_extra_copy_of_an_owned_uuid_is_rejected(self):
        ref = "synthetic-duplicate"
        self.inventories["objects"][0]["items"].append(
            {"ref": ref, "name": "Copied source object", "rna_type": "Object"})
        self.customs[ref] = copy.deepcopy(self.customs[self.object_ref()])
        self.customs[ref][0]["ref"] = ref
        with self.assertRaisesRegex(contract.SceneContractError, "unexpected native entity"):
            self.check()

    def test_name_match_cannot_replace_missing_complete_uuid(self):
        items = self.customs[self.object_ref()][0]["items"]
        items[:] = [item for item in items if item["key"] != "motionwright.node_id"]
        with self.assertRaisesRegex(contract.SceneContractError, "identity disagrees"):
            self.check()

    def test_material_from_another_scene_is_rejected(self):
        ref = self.refs[("materials", self.plan["contribution"]["objects"][0]["material_name"])]
        item = next(item for item in self.customs[ref][0]["items"] if item["key"] == "motionwright.scene_id")
        item["value"] = "00000000-0000-4000-8000-000000000099"
        with self.assertRaisesRegex(contract.SceneContractError, "identity disagrees"):
            self.check()

    def test_camera_cannot_forge_a_canvas_node_uuid(self):
        ref = self.refs[("objects", self.plan["camera"]["object_name"])]
        self.customs[ref][0]["items"].append(
            {"key": "motionwright.node_id", "value": self.plan["contribution"]["objects"][0]["node_id"],
             "status": "managed"})
        with self.assertRaisesRegex(contract.SceneContractError, "identity disagrees"):
            self.check()

    def test_prior_source_revision_is_rejected(self):
        item = next(item for item in self.customs[self.object_ref()][0]["items"]
                    if item["key"] == "motionwright.revision")
        item["value"] = "6"
        with self.assertRaises(contract.SceneContractError):
            self.check()

    def test_native_generation_change_during_inventory_is_rejected(self):
        self.inventories["materials"][0]["generation"] = 9
        with self.assertRaisesRegex(contract.SceneContractError, "generation changed"):
            self.check()

    def test_bool_generation_is_not_an_integer_generation(self):
        with self.assertRaisesRegex(contract.SceneContractError, "invalid semantic generation"):
            contract.check_identity_readback(self.plan, True, self.inventories, self.customs)

    def test_incomplete_or_falsely_terminal_inventory_is_rejected(self):
        self.inventories["objects"][0]["truncated"] = True
        with self.assertRaisesRegex(contract.SceneContractError, "incomplete getter pages"):
            self.check()

    def test_exhaustive_custom_inventory_is_required(self):
        del self.customs[self.object_ref()]
        with self.assertRaisesRegex(contract.SceneContractError, "not exhaustive"):
            self.check()

    def test_custom_getter_for_another_ref_is_rejected(self):
        self.customs[self.object_ref()][0]["ref"] = "foreign"
        with self.assertRaisesRegex(contract.SceneContractError, "another native entity"):
            self.check()

    def test_deceptive_unsupported_identity_sidecar_is_rejected(self):
        self.customs[self.object_ref()][0]["items"][0]["status"] = "unsupported_by_design"
        with self.assertRaisesRegex(contract.SceneContractError, "not readable"):
            self.check()

    def test_source_identity_table_cannot_override_authoritative_source_fields(self):
        self.plan["identities"][-1]["properties"]["motionwright.node_id"] = "foreign"
        with self.assertRaisesRegex(contract.SceneContractError, "canonical source fields"):
            self.check()

    def test_source_plan_cannot_drop_a_canvas_object(self):
        source = json.loads(self.raw)
        source["scenes"][0]["nodes"].append({"id": "00000000-0000-4000-8000-000000000005"})
        raw = json.dumps(source, separators=(",", ":")).encode()
        self._rebind_digest(raw)
        with self.assertRaisesRegex(contract.SceneContractError, "dropped or invented"):
            contract.check_project_source(self.plan, raw)

    def _rebind_digest(self, raw):
        digest = hashlib.sha256(raw).hexdigest()
        self.plan["project_json_sha256"] = digest
        for item in self.plan["identities"]:
            item["properties"]["motionwright.project_json_sha256"] = digest

    def test_source_bytes_with_another_digest_are_rejected(self):
        with self.assertRaisesRegex(contract.SceneContractError, "digest differs"):
            contract.check_project_source(self.plan, self.raw + b" ")

    def test_duplicate_project_json_member_is_rejected_even_with_matching_digest(self):
        raw = b'{"id":"first","id":"second"}'
        self._rebind_digest(raw)
        with self.assertRaisesRegex(contract.SceneContractError, "duplicate JSON member"):
            contract.check_project_source(self.plan, raw)


class GetterBindingTests(unittest.TestCase):
    def test_static_camera_properties_are_explicit_and_source_derived(self):
        plan, *_ = fixture()
        result = contract.expected_camera_properties(plan)
        self.assertEqual(result["camera_data"]["sensor_fit"], "VERTICAL")
        self.assertEqual(result["camera_data"]["ortho_scale"], 10.8)
        self.assertEqual(result["render_settings"]["resolution_x"], 1920)
        self.assertEqual(result["render_settings"]["pixel_aspect_y"], 1.0)

    def test_property_getter_for_another_object_is_rejected(self):
        with self.assertRaisesRegex(contract.SceneContractError, "another entity"):
            contract.check_property_getters("camera", {"ortho_scale": 10.8},
                                            [property_get("other", "ortho_scale", 10.8)])

    def test_boolean_and_nonfinite_values_cannot_pass_numeric_getters(self):
        for value in (True, float("nan"), float("inf"), 10.80001):
            with self.subTest(value=value), self.assertRaises(contract.SceneContractError):
                contract.check_property_getters("camera", {"ortho_scale": 10.8},
                                                [property_get("camera", "ortho_scale", value)])

    def test_integer_settings_are_not_accepted_as_nearby_floats(self):
        with self.assertRaises(contract.SceneContractError):
            contract.check_property_getters("render", {"resolution_x": 1920},
                                            [property_get("render", "resolution_x", 1919.9999999)])

    def test_missing_duplicate_or_mislabelled_property_is_rejected(self):
        bad = property_get("camera", "ortho_scale", 10.8)
        bad["descriptor"]["id"] = "clip_end"
        with self.assertRaisesRegex(contract.SceneContractError, "descriptor"):
            contract.check_property_getters("camera", {"ortho_scale": 10.8}, [bad])
        with self.assertRaisesRegex(contract.SceneContractError, "missing property"):
            contract.check_property_getters("camera", {"ortho_scale": 10.8}, [])

    def test_relation_binds_parent_property_membership_and_generation(self):
        rows = relation("scene", ["camera"], property_name="camera")
        self.assertEqual(contract.check_relation_pages(rows, "scene", "camera", 8, {"camera"}, exact=True),
                         {"camera"})
        for ref, prop, gen, targets in [
                ("other-scene", "camera", 8, {"camera"}),
                ("scene", "objects", 8, {"camera"}),
                ("scene", "camera", 9, {"camera"}),
                ("scene", "camera", 8, {"other-camera"})]:
            with self.subTest(ref=ref, prop=prop, gen=gen, targets=targets):
                with self.assertRaises(contract.SceneContractError):
                    contract.check_relation_pages(rows, ref, prop, gen, targets, exact=True)

    def test_duplicate_relation_target_and_extra_membership_are_rejected(self):
        with self.assertRaisesRegex(contract.SceneContractError, "duplicate relation"):
            contract.check_relation_pages(relation("collection", ["object", "object"]),
                                          "collection", "objects", 8, {"object"}, exact=True)
        with self.assertRaisesRegex(contract.SceneContractError, "membership"):
            contract.check_relation_pages(relation("collection", ["object", "extra"]),
                                          "collection", "objects", 8, {"object"}, exact=True)


    def test_unsupported_or_malformed_camera_source_is_rejected(self):
        for changes in ({"zoom": 1.2}, {"rotation_deg": 15.0}, {"zoom": True},
                        {"center_x": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(contract.SceneContractError):
                plan, *_ = fixture()
                plan["camera"]["source"].update(changes)
                contract.expected_camera_properties(plan)

    def test_runtime_owned_descriptor_cannot_be_used_as_property_evidence(self):
        value = property_get("camera", "ortho_scale", 10.8)
        value["descriptor"]["status"] = "runtime_owned"
        with self.assertRaisesRegex(contract.SceneContractError, "descriptor"):
            contract.check_property_getters("camera", {"ortho_scale": 10.8}, [value])

    def test_relation_request_offset_bool_is_rejected(self):
        rows = relation("scene", ["camera"], property_name="camera")
        rows[0]["args"]["offset"] = False
        with self.assertRaisesRegex(contract.SceneContractError, "offset"):
            contract.check_relation_pages(rows, "scene", "camera", 8, {"camera"}, exact=True)


class SourceCustodyTests(unittest.TestCase):
    def setUp(self):
        root = Path(os.environ.get("MOTIONWRIGHT_BLENDER_CONTRACT_TEST_ROOT", Path(__file__).parent))
        self.temporary = tempfile.TemporaryDirectory(prefix="scene-contract-unit-", dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.path = self.workspace / "original.blend"
        # Opaque bytes for custody unit tests; deliberately not a Blender file.
        self.payload = b"synthetic-custody-unit-input-not-a-native-scene\n"
        self.path.write_bytes(self.payload)

    def pin(self):
        return contract.inspect_source_file(self.workspace, "original.blend")

    def test_descriptor_read_matches_original_bytes_without_modification(self):
        pin = self.pin()
        self.assertEqual(pin, {"relative_path": "original.blend",
                               "sha256": hashlib.sha256(self.payload).hexdigest(),
                               "bytes": len(self.payload)})
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_new_output_preflight_never_overwrites_an_existing_source(self):
        with self.assertRaisesRegex(contract.SceneContractError, "already exists"):
            contract.require_new_source_path(self.workspace, "original.blend")
        self.assertEqual(self.path.read_bytes(), self.payload)
        new = contract.require_new_source_path(self.workspace, "edited.blend")
        self.assertFalse(new.exists())

    def test_workspace_escape_and_alias_spellings_are_rejected(self):
        for path in ("../original.blend", "/original.blend", "./original.blend",
                     "x/../original.blend", "x//source.blend", "C:/source.blend", r"x\source.blend"):
            with self.subTest(path=path), self.assertRaises(contract.SceneContractError):
                contract.inspect_source_file(self.workspace, path)

    def test_hardlinked_source_is_rejected_without_reading_an_alias(self):
        os.link(self.path, self.workspace / "alias.blend")
        with self.assertRaisesRegex(contract.SceneContractError, "single-link"):
            self.pin()

    def test_digest_and_size_mismatch_preserve_original_bytes(self):
        for kwargs in ({"expected_sha256": "0" * 64},
                       {"expected_bytes": len(self.payload) + 1},
                       {"expected_sha256": "UPPERCASE"},
                       {"expected_bytes": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(contract.SceneContractError):
                contract.inspect_source_file(self.workspace, "original.blend", **kwargs)
        self.assertEqual(self.path.read_bytes(), self.payload)

    def test_mutation_during_descriptor_read_is_not_accepted_as_stable_custody(self):
        original_read = os.read
        injected = False

        def changing_read(fd, count):
            nonlocal injected
            data = original_read(fd, count)
            if not injected:
                injected = True
                with self.path.open("ab") as target:
                    target.write(b"changed while read")
            return data

        with mock.patch.object(contract.os, "read", side_effect=changing_read):
            with self.assertRaisesRegex(contract.SceneContractError, "changed while"):
                self.pin()
        self.assertTrue(injected)
        self.assertTrue(self.path.read_bytes().endswith(b"changed while read"))

    def test_file_open_requires_exact_path_disabled_scripts_and_unchanged_original(self):
        pin = self.pin()
        response = {"changed": True, "path": "original.blend", "use_scripts": False}
        contract.check_file_open_response("original.blend", response, pin, dict(pin))
        bad = dict(response, use_scripts=True)
        with self.assertRaises(contract.SceneContractError):
            contract.check_file_open_response("original.blend", bad, pin, pin)
        bad = dict(response, path="different.blend")
        with self.assertRaises(contract.SceneContractError):
            contract.check_file_open_response("original.blend", bad, pin, pin)
        changed = dict(pin, sha256="f" * 64)
        with self.assertRaisesRegex(contract.SceneContractError, "source identity changed"):
            contract.check_file_open_response("original.blend", response, pin, changed)

    def test_missing_open_response_is_never_inferred_from_file_presence(self):
        pin = self.pin()
        with self.assertRaisesRegex(contract.SceneContractError, "exact scoped contract"):
            contract.check_file_open_response("original.blend", {}, pin, pin)


if __name__ == "__main__":
    unittest.main()
