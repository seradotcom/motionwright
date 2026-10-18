"""Source custody and editable receiver conformance tests without Blender.

Temporary fixtures stay within this test tree. These unit tests do not
claim a real Blender application import or native-file reopen.
"""
from __future__ import annotations
import copy
import hashlib
import json
import pathlib
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from native_blender_editable_receiver import (
    EDIT_DELTA_X, LEGACY_SCHEMA, SCHEMA, PHASES, ReceiverError,
    bounded_source_snapshot, compare_edit, compare_reimported_glb,
    digest, original_glb, receipt_schemas_compatible,
    root_path, read_json, within_root, write_receipt,
)

A="a"*12
B="b"*32
PANEL="Motionwright-panel-"+A
FOCUS="Motionwright-focus-"+B


def original_two_object_glb(**changes):
    """Tiny inert glTF binary fixture, not Blender-originated geometry."""
    data={
        "asset":{"version":"2.0"},
        "buffers":[{"byteLength":16}],
        "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":16}],
        "scenes":[{"nodes":[0,1]}],
        "scene":0,
        "nodes":[{"name":PANEL,"mesh":0},{"name":FOCUS,"mesh":1}],
        "meshes":[{"name":"Plane","primitives":[{"attributes":{"POSITION":0}}]},
                  {"name":"Cylinder","primitives":[{"attributes":{"POSITION":1}}]}],
        "materials":[{"name":PANEL+"-material"},{"name":FOCUS+"-material"}],
    }
    data.update(changes)
    raw=json.dumps(data,sort_keys=True,separators=(",",":")).encode("utf8")
    raw+=b" "*((-len(raw))%4)
    binary=b"\x00"*16
    return (struct.pack("<4sII",b"glTF",2,12+8+len(raw)+8+len(binary))
            +struct.pack("<II",len(raw),0x4E4F534A)+raw
            +struct.pack("<II",len(binary),0x004E4942)+binary)


def snapshot():
    return [
        {"label":"focus","name":FOCUS,"type":"MESH","vertices":192,"faces":124,
         "location":[0.7,-0.7,.01],"dimensions":[2.2,2.2,.01],
         "material":{"name":FOCUS+"-material","color":[0.75,0.5,.2,1.]}},
        {"label":"panel","name":PANEL,"type":"MESH","vertices":4,"faces":2,
         "location":[-4.4,-2.3,.01],"dimensions":[5.2,2.6,0.],
         "material":{"name":PANEL+"-material","color":[0.08,0.3,.52,1.]}},
    ]


class BlenderEditableReceiverPureTests(unittest.TestCase):
    def setUp(self):
        # Use only the authorized Motionwright test tree on the Windows
        # workstation; never default to C:\Users\...\AppData\Local\Temp.
        self.tmp=tempfile.TemporaryDirectory(dir=str(pathlib.Path(__file__).resolve().parent))
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        self.original=self.root/"original.glb"
        self.original.write_bytes(original_two_object_glb())

    def test_first_party_GLTF_has_bounded_two_source_objects(self):
        ref=original_glb(self.original,digest(self.original.read_bytes()))
        self.assertEqual(set(ref["source_object_names"]),{"focus","panel"})
        self.assertEqual(ref["source_object_names"]["panel"],PANEL)
        self.assertEqual(ref["source_object_names"]["focus"],FOCUS)
        self.assertEqual(ref["original_Project_UUID_custody"],
                         "NOT_PROVEN_BY_GLB_NAME_ALONE")

    def test_source_requires_exact_sha_and_not_a_display_name(self):
        with self.assertRaisesRegex(ReceiverError,"SHA_changed"):
            original_glb(self.original,"0"*64)
        with self.assertRaisesRegex(ReceiverError,"digest_required"):
            original_glb(self.original,"invalid")

    def test_truncated_source_GLTF_is_rejected(self):
        corrupted=original_two_object_glb()[:-4]
        self.original.write_bytes(corrupted)
        with self.assertRaisesRegex(ReceiverError,"GLB_header_mismatch"):
            original_glb(self.original,digest(corrupted))

    def test_reject_import_from_external_glTF_buffer_or_extension(self):
        for added in ({"buffers":[{"byteLength":16,"uri":"https://evil.invalid/data.bin"}]},
                      {"extensionsUsed":["EXT_external_risk"]},
                      {"images":[{"uri":"https://evil.invalid/x.png"}]},
                      {"animations":[{}]}):
            with self.subTest(added=added):
                raw=original_two_object_glb(**added)
                self.original.write_bytes(raw)
                with self.assertRaises(ReceiverError):
                    original_glb(self.original,digest(raw))

    def test_source_missing_cylinder_or_duplicate_identity_is_rejected(self):
        for nodes in (
            [{"name":PANEL,"mesh":0},{"name":"Foreign-"+B,"mesh":1}],
            [{"name":PANEL,"mesh":0},{"name":PANEL,"mesh":1}]
        ):
            raw=original_two_object_glb(nodes=nodes)
            self.original.write_bytes(raw)
            with self.assertRaisesRegex(ReceiverError,
                                       "source_identity_incomplete|object_identity|shape_name"):
                original_glb(self.original,digest(raw))

    def test_retained_imported_blender_mesh_and_pbr_passes(self):
        clean=bounded_source_snapshot(snapshot())
        self.assertEqual(set(clean),{"panel","focus"})
        self.assertEqual(clean["panel"]["dimensions"][-1],0.,
                         "A genuine planar object has one zero-dimensional axis")

    def test_edited_and_reopened_scene_preserves_other_source_semantics(self):
        before=snapshot()
        after=copy.deepcopy(before)
        after[0]["location"][0]=round(after[0]["location"][0]+EDIT_DELTA_X,6)
        proof=compare_edit(before,after)
        self.assertTrue(proof["material_and_topology_preserved"])
        self.assertFalse(proof["Project_UUID_and_original_blend_authority"]=="PROVEN")

    def test_exported_glb_reimport_preserves_edited_native_geometry_and_PBR(self):
        original=snapshot()
        edited=copy.deepcopy(original)
        edited[0]["location"][0]+=EDIT_DELTA_X
        reimport=copy.deepcopy(edited)
        reimport[0]["location"][0]+=0.0002
        reimport[1]["material"]["color"][0]+=0.001
        state=compare_reimported_glb(original,edited,reimport)
        self.assertTrue(state[
            "original_GLB_to_saved_Blend_to_edit_to_new_GLB_to_new_Blend"])
        self.assertFalse(state[
            "original_Project_UUID_binding_or_Motionwright_blend_source"])

    def test_reimported_glb_loses_edit_or_shape_is_rejected(self):
        original=snapshot()
        edited=copy.deepcopy(original)
        edited[0]["location"][0]+=EDIT_DELTA_X
        with self.assertRaisesRegex(ReceiverError,"lost_edited_position"):
            compare_reimported_glb(original,edited,original)
        wrong=copy.deepcopy(edited)
        wrong[0]["faces"]-=2
        with self.assertRaises(ReceiverError):
            compare_reimported_glb(original,edited,wrong)

    def test_reimported_glb_material_drift_is_rejected(self):
        original=snapshot()
        edited=copy.deepcopy(original)
        edited[0]["location"][0]+=EDIT_DELTA_X
        wrong=copy.deepcopy(edited)
        wrong[1]["material"]["color"][0]+=0.06
        with self.assertRaisesRegex(ReceiverError,"PBR_color_changed"):
            compare_reimported_glb(original,edited,wrong)

    def test_missing_actual_edit_is_not_accepted(self):
        with self.assertRaisesRegex(ReceiverError,"transform_did_not_persist"):
            compare_edit(snapshot(),snapshot())

    def test_unrelated_panel_move_is_not_accepted_as_focus_edit(self):
        after=copy.deepcopy(snapshot())
        after[0]["location"][0]+=EDIT_DELTA_X
        after[1]["location"][0]+=0.2
        with self.assertRaisesRegex(ReceiverError,"unapproved_semantic_change|transform"):
            compare_edit(snapshot(),after)

    def test_original_material_mutation_is_rejected(self):
        after=copy.deepcopy(snapshot())
        after[0]["location"][0]+=EDIT_DELTA_X
        after[0]["material"]["color"][0]=0.0
        with self.assertRaisesRegex(ReceiverError,"unapproved_semantic_change:material"):
            compare_edit(snapshot(),after)

    def test_edited_scene_drops_real_triangle_mesh_is_rejected(self):
        after=copy.deepcopy(snapshot())
        after[0]["location"][0]+=EDIT_DELTA_X
        after[0]["faces"]=120
        with self.assertRaisesRegex(ReceiverError,"lost_source_mesh_topology"):
            compare_edit(snapshot(),after)

    def test_forged_shape_name_or_duplicate_source_node_is_rejected(self):
        after=copy.deepcopy(snapshot())
        after[0]["name"]="Unknown_"+B
        with self.assertRaisesRegex(ReceiverError,"original_node_name"):
            bounded_source_snapshot(after)
        after=copy.deepcopy(snapshot())
        after[0]["label"]="panel"
        with self.assertRaisesRegex(
            ReceiverError,"missing_or_duplicated|lost_source_mesh_topology"
        ):
            bounded_source_snapshot(after)

    def test_nonfinite_or_zero_dimensions_cannot_form_valid_snapshot(self):
        after=copy.deepcopy(snapshot())
        after[1]["dimensions"]=[0.,0.,0.]
        with self.assertRaisesRegex(ReceiverError,"geometry_or_world_transform"):
            bounded_source_snapshot(after)
        after=copy.deepcopy(snapshot())
        after[1]["location"][0]=float("nan")
        with self.assertRaisesRegex(ReceiverError,"geometry_or_world_transform"):
            bounded_source_snapshot(after)

    def test_no_original_source_blend_authority_is_fabricated(self):
        edited=copy.deepcopy(snapshot())
        edited[0]["location"][0]+=EDIT_DELTA_X
        proof=compare_edit(snapshot(),edited)
        self.assertEqual(proof["Project_UUID_and_original_blend_authority"],"NOT_PROVEN")

    def test_fresh_private_destination_and_files_are_bounded(self):
        self.assertEqual(root_path(str(self.root)),self.root.resolve())
        output=within_root(self.root/"original.blend",self.root.resolve())
        self.assertFalse(output.exists())
        output.write_bytes(b"BLENDER-CONTROL-FAKE-BYTES")
        with self.assertRaisesRegex(ReceiverError,"already_exists"):
            within_root(output,self.root.resolve())
        with self.assertRaisesRegex(ReceiverError,"outside_private_root"):
            within_root(self.root.parent/"escape.blend",self.root.resolve())

    def test_duplicate_JSON_receipt_keys_rejected_and_no_overwrite(self):
        file=self.root/"import.json"
        file.write_text('{"phase": "import", "phase": "forge"}',encoding="utf8")
        with self.assertRaisesRegex(ReceiverError,"duplicate_receipt_field"):
            read_json(file)
        with self.assertRaises(FileExistsError):
            write_receipt(file,{"valid":False})

    def test_product_receipt_version_and_legacy_compatibility_are_explicit(self):
        self.assertEqual(SCHEMA,"motionwright.native.blender-editable-receiver/2")
        self.assertEqual(PHASES,(
            "import","edit","verify","reimport","reopen-reimport"
        ))
        self.assertEqual(LEGACY_SCHEMA,
                         "motionwright.a4.blender-first-party-interchange-reopen/1")
        self.assertTrue(receipt_schemas_compatible(
            {"schema": LEGACY_SCHEMA, "phase":"imported_first_party_GLB_to_Blend"},
            {"schema": SCHEMA, "phase":"original_Blend_reopened_and_edited"},
            {"schema": SCHEMA, "phase":"edited_Blend_reopened_in_new_Blender_process"}
        ))
        self.assertFalse(receipt_schemas_compatible({"schema":"unrecognized/99"}))
        self.assertFalse(receipt_schemas_compatible({"source":"forged"}))
        self.assertFalse(receipt_schemas_compatible({"schema":SCHEMA}, "raw"))
        self.assertFalse(receipt_schemas_compatible({"schema":None}))

    def test_fifth_stage_requires_reimported_geometry_and_edit_together(self):
        initial=snapshot()
        changed=copy.deepcopy(initial)
        changed[0]["location"][0]=round(
            changed[0]["location"][0]+EDIT_DELTA_X,6)
        same=copy.deepcopy(changed)
        self.assertTrue(compare_reimported_glb(initial,changed,same)[
            "original_GLB_to_saved_Blend_to_edit_to_new_GLB_to_new_Blend"])
        # Exported GLB that omits the user's change must never count as a
        # completed fifth process even if an importer successfully opens it.
        with self.assertRaisesRegex(ReceiverError,
                                    "GLTF_reimport_lost_edited_position"):
            compare_reimported_glb(initial,changed,initial)
        # A new mesh ID or silently recolored object is equally unacceptable.
        altered=copy.deepcopy(same)
        altered[1]["name"]="Motionwright-panel-"+"f"*12
        altered[1]["material"]["name"]=altered[1]["name"]+"-material"
        with self.assertRaisesRegex(ReceiverError,
                                    "GLTF_reimport_lost_edited_topology_or_original_name"):
            compare_reimported_glb(initial,changed,altered)
        altered=copy.deepcopy(same)
        altered[0]["material"]["color"][0]=0.0
        with self.assertRaisesRegex(ReceiverError,
                                    "GLTF_reimport_material_PBR_color_changed"):
            compare_reimported_glb(initial,changed,altered)

    def test_witness_report_json_uses_original_source_bytes_not_provider_claims(self):
        file=self.root/"import.json"
        original=original_glb(self.original,digest(self.original.read_bytes()))
        write_receipt(file,{
            "schema":SCHEMA,
            "phase":"imported_first_party_GLB_to_Blend",
            "source":original,
            "objects":snapshot(),
            "original_blend_originated_from_GLB_not_Studio_source":True,
            "rights_human_acceptance":False,
        })
        restored=read_json(file)
        self.assertEqual(restored["source"]["sha256"],digest(self.original.read_bytes()))
        self.assertFalse(restored["rights_human_acceptance"])
        self.assertTrue(restored["original_blend_originated_from_GLB_not_Studio_source"])


if __name__=="__main__":
    unittest.main()
