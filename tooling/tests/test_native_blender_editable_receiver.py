"""Pure source custody and private editable receiver negative tests, no bpy.

All temporary bytes are created under E:/Projects/motionwright/...
These tests do not claim Blender imported an original GLB or reopened .blend.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from types import SimpleNamespace
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import native_blender_editable_receiver as receiver
from native_blender_editable_receiver import (
    EDIT_DELTA_X, ReceiverError, SCHEMA, LEGACY_RECEIPT_SCHEMA,
    bounded_source_snapshot, compare_edit, compare_reimported_glb,
    digest, original_glb, root_path, read_json, supported_receipt_schema,
    within_root, write_receipt, source_scene_snapshot,
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
        "materials":[{"name":name+"-material","doubleSided":True,
                      "pbrMetallicRoughness":{"baseColorFactor":[.2,.3,.4,1.]}}
                     for name in (PANEL,FOCUS)],
    }
    data.update(changes)
    raw=json.dumps(data,sort_keys=True,separators=(",",":")).encode("utf8")
    raw+=b" "*((-len(raw))%4)
    binary=b"\x00"*16
    return (struct.pack("<4sII",b"glTF",2,12+8+len(raw)+8+len(binary))
            +struct.pack("<II",len(raw),0x4E4F534A)+raw
            +struct.pack("<II",len(binary),0x004E4942)+binary)


def mesh_fixture(label):
    if label == "panel":
        positions = [[-2.6,-1.3,0.], [2.6,-1.3,0.],
                     [2.6,1.3,0.], [-2.6,1.3,0.]]
        triangles = [[0,1,2], [0,2,3]]
    else:
        ring = [[round(1.1*math.cos(2*math.pi*i/32),7),
                 round(1.1*math.sin(2*math.pi*i/32),7)]
                for i in range(32)]
        positions, triangles = [], []
        for i in range(32):
            a, b = ring[i], ring[(i+1)%32]
            k = len(positions)
            positions.extend([[*a,-.005], [*b,-.005], [*b,.005], [*a,.005]])
            triangles.extend([[k,k+1,k+2], [k,k+2,k+3]])
        for z in (-.005, .005):
            k = len(positions)
            positions.extend([[*point,z] for point in ring])
            for i in range(1,31):
                triangle = [k,k+i,k+i+1]
                triangles.append(triangle if z > 0 else list(reversed(triangle)))
    normals = []
    for face in triangles:
        a,b,c = [positions[index] for index in face]
        u,v = [[p-q for p,q in zip(point,a)] for point in (b,c)]
        n = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2],
             u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(x*x for x in n))
        normals.append([[round(x/length,7) for x in n] for _ in range(3)])
    uv0=[[[round((positions[index][0]+3)/6,7),
            round((positions[index][1]+3)/6,7)] for index in face]
         for face in triangles]
    return {"positions":positions,"triangles":triangles,"corner_normals":normals,
            "corner_uv0":uv0}


def snapshot():
    result = []
    for label,name,location,dimensions,color in (
        ("focus",FOCUS,[.7,-.7,.01],[2.2,2.2,.01],[.75,.5,.2,1.]),
        ("panel",PANEL,[-4.4,-2.3,.01],[5.2,2.6,0.],[.08,.3,.52,1.]),
    ):
        mesh = mesh_fixture(label)
        result.append({
            "label":label,"name":name,"type":"MESH",
            "vertices":len(mesh["positions"]),"faces":len(mesh["triangles"]),
            "location":location,"dimensions":dimensions,"mesh":mesh,
            "content_snapshot_version":1,
            "visibility":{"hide_render":False,"hide_viewport":False,
                          "hide_get":False,"visible_get":True},
            "matrix_world":[[1.,0.,0.,location[0]],[0.,1.,0.,location[1]],
                            [0.,0.,1.,location[2]],[0.,0.,0.,1.]],
            "material":{"name":name+"-material","color":color,
                        "render_settings":{"surface_render_method":"DITHERED",
                            "use_transparency_overlap":True,"use_backface_culling":False,
                            "use_backface_culling_shadow":False,
                            "use_backface_culling_lightprobe_volume":True,
                            "use_transparent_shadow":True,"use_thickness_from_shadow":False},
                        "shader_inputs":{"Base Color":color,"Metallic":0.,
                                         "Roughness":.5,"Alpha":1.}},
        })
    return result


def move_focus(objects):
    objects[0]["location"][0] = round(objects[0]["location"][0]+EDIT_DELTA_X,6)
    objects[0]["matrix_world"][0][3] = objects[0]["location"][0]
    return objects


class UVLayers(list):
    @property
    def active(self):
        return self[0] if self else None


class SocketMap(dict):
    def __iter__(self):
        return iter(self.values())


def fake_scene(objects=None):
    """A source-API contract fixture, never a Blender execution receipt."""
    output = []
    for item in copy.deepcopy(objects if objects is not None else snapshot()):
        inputs = SocketMap({key:SimpleNamespace(identifier=key,default_value=value,
                                                is_linked=False)
                            for key,value in item["material"]["shader_inputs"].items()})
        bsdf = SimpleNamespace(type="BSDF_PRINCIPLED",inputs=inputs)
        surface = SimpleNamespace(links=[SimpleNamespace(from_node=bsdf)])
        material_output = SimpleNamespace(type="OUTPUT_MATERIAL",is_active_output=True,target="ALL",
                                           inputs={"Surface":surface})
        material = SimpleNamespace(name=item["material"]["name"],use_nodes=True,
                                   animation_data=None,
                                   node_tree=SimpleNamespace(nodes=[bsdf,material_output],
                                                             animation_data=None),
                                   **item["material"]["render_settings"])
        mesh = item["mesh"]
        polygons = [SimpleNamespace(vertices=face,material_index=0,
                                   loop_indices=list(range(3*i,3*i+3)))
                    for i,face in enumerate(mesh["triangles"])]
        normals = [SimpleNamespace(vector=value)
                   for face in mesh["corner_normals"] for value in face]
        uv0=[SimpleNamespace(uv=value) for face in mesh["corner_uv0"] for value in face]
        data = SimpleNamespace(vertices=[SimpleNamespace(co=value) for value in mesh["positions"]],
                               uv_layers=UVLayers([SimpleNamespace(data=uv0)]),
                               polygons=polygons,materials=[material],corner_normals=normals,
                               loops=[None]*len(normals),shape_keys=None,animation_data=None)
        output.append(SimpleNamespace(
            name=item["name"],type="MESH",parent=None,data=data,active_material=material,
            location=item["location"],dimensions=item["dimensions"],
            matrix_world=item["matrix_world"],modifiers=[],constraints=[],animation_data=None,
            hide_render=False,hide_viewport=False,
            hide_get=lambda:False,visible_get=lambda:True))
    collection=SimpleNamespace(hide_render=False,hide_viewport=False,children=[])
    return SimpleNamespace(context=SimpleNamespace(
        scene=SimpleNamespace(objects=output,animation_data=None,collection=collection)))



class BlenderEditableReceiverPureTests(unittest.TestCase):
    def setUp(self):
        # Keep all test temporaries inside this disposable worktree rather
        # than an OS-global temp folder or an actual user's Blender project.
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
        after[0]["matrix_world"][0][3]=after[0]["location"][0]
        proof=compare_edit(before,after)
        self.assertTrue(proof["material_and_topology_preserved"])
        self.assertFalse(proof["Project_UUID_and_original_blend_authority"]=="PROVEN")

    def test_exported_glb_reimport_preserves_edited_native_geometry_and_PBR(self):
        original=snapshot()
        edited=copy.deepcopy(original)
        edited[0]["location"][0]+=EDIT_DELTA_X
        edited[0]["matrix_world"][0][3]=edited[0]["location"][0]
        reimport=copy.deepcopy(edited)
        reimport[0]["location"][0]+=0.0002
        reimport[0]["matrix_world"][0][3]=reimport[0]["location"][0]
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
        edited[0]["matrix_world"][0][3]=edited[0]["location"][0]
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
        edited[0]["matrix_world"][0][3]=edited[0]["location"][0]
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
        after[0]["matrix_world"][0][3]=after[0]["location"][0]
        after[1]["location"][0]+=0.2
        after[1]["matrix_world"][0][3]=after[1]["location"][0]
        with self.assertRaisesRegex(ReceiverError,"unapproved_semantic_change|transform"):
            compare_edit(snapshot(),after)

    def test_original_material_mutation_is_rejected(self):
        after=copy.deepcopy(snapshot())
        after[0]["location"][0]+=EDIT_DELTA_X
        after[0]["matrix_world"][0][3]=after[0]["location"][0]
        after[0]["material"]["color"][0]=0.0
        with self.assertRaisesRegex(ReceiverError,"unapproved_semantic_change:material"):
            compare_edit(snapshot(),after)

    def test_edited_scene_drops_real_triangle_mesh_is_rejected(self):
        after=copy.deepcopy(snapshot())
        after[0]["location"][0]+=EDIT_DELTA_X
        after[0]["matrix_world"][0][3]=after[0]["location"][0]
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
        after[1]["matrix_world"][0][3]=after[1]["location"][0]
        with self.assertRaisesRegex(ReceiverError,"geometry_or_world_transform"):
            bounded_source_snapshot(after)

    def test_no_original_source_blend_authority_is_fabricated(self):
        edited=copy.deepcopy(snapshot())
        edited[0]["location"][0]+=EDIT_DELTA_X
        edited[0]["matrix_world"][0][3]=edited[0]["location"][0]
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

    def test_new_receipt_uses_permanent_product_schema(self):
        self.assertEqual(SCHEMA,
            "motionwright.native.blender-editable-interchange/1")
        self.assertNotIn(".a4.",SCHEMA)
        self.assertTrue(supported_receipt_schema(SCHEMA))
        self.assertFalse(supported_receipt_schema("motionwright.native.unsupported/1"))
        self.assertFalse(supported_receipt_schema(4))

    def test_legacy_receipt_can_be_read_without_rewriting_provenance(self):
        # The earlier draft's already-created receipts keep their exact bytes
        # and source/media hashes. Only compatibility checking is permitted;
        # newly written product receipts always use SCHEMA.
        legacy={
            "schema": LEGACY_RECEIPT_SCHEMA,
            "phase": "imported_first_party_GLB_to_Blend",
            "source":{"sha256":digest(self.original.read_bytes())},
            "rights_human_acceptance":False
        }
        p=self.root/"legacy.json"
        write_receipt(p,legacy)
        before=digest(p.read_bytes())
        retrieved=read_json(p)
        self.assertEqual(retrieved,legacy)
        self.assertTrue(supported_receipt_schema(retrieved["schema"]))
        self.assertEqual(before,digest(p.read_bytes()))
        self.assertNotEqual(retrieved["schema"],SCHEMA)

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

    def test_unmeasured_legacy_snapshot_cannot_inherit_full_content_proof(self):
        old=snapshot()
        for node in old:
            del node["mesh"]
            del node["content_snapshot_version"]
        with self.assertRaisesRegex(ReceiverError,"missing_full_content_measurement"):
            bounded_source_snapshot(old)

    def test_same_counts_and_bounds_do_not_hide_changed_vertex(self):
        original=snapshot()
        changed=move_focus(copy.deepcopy(original))
        changed[0]["mesh"]["positions"][12][0]+=.03
        with self.assertRaisesRegex(ReceiverError,"unapproved_semantic_change:mesh"):
            compare_edit(original,changed)

    def test_same_counts_do_not_hide_changed_face_connectivity(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        corrupted=copy.deepcopy(edited)
        corrupted[0]["mesh"]["triangles"][20][0]=4
        with self.assertRaisesRegex(ReceiverError,"vertex_winding_or_normal_changed"):
            compare_reimported_glb(original,edited,corrupted)

    def test_glb_reindexing_and_cyclic_triangle_order_preserve_geometry(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        reimport=copy.deepcopy(edited)
        for node in reimport:
            mesh=node["mesh"]
            count=len(mesh["positions"])
            mesh["positions"].reverse()
            mesh["triangles"]=[[count-1-index for index in face] for face in mesh["triangles"]]
            mesh["triangles"]=[face[1:]+face[:1] for face in mesh["triangles"]]
            mesh["corner_normals"]=[face[1:]+face[:1] for face in mesh["corner_normals"]]
            mesh["corner_uv0"]=[face[1:]+face[:1] for face in mesh["corner_uv0"]]
            mesh["triangles"].reverse()
            mesh["corner_normals"].reverse()
            mesh["corner_uv0"].reverse()
        self.assertTrue(compare_reimported_glb(original,edited,reimport)[
            "original_GLB_to_saved_Blend_to_edit_to_new_GLB_to_new_Blend"])

    def test_glb_equivalent_vertex_split_does_not_require_index_identity(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        reimport=copy.deepcopy(edited)
        mesh=reimport[0]["mesh"]
        face=mesh["triangles"][0]
        mesh["positions"].append(copy.deepcopy(mesh["positions"][face[0]]))
        face[0]=len(mesh["positions"])-1
        reimport[0]["vertices"]+=1
        compare_reimported_glb(original,edited,reimport)

    def test_glb_winding_and_corner_normal_changes_are_rejected(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        for change in ("winding","normal"):
            with self.subTest(change=change):
                corrupted=copy.deepcopy(edited)
                if change=="winding":
                    corrupted[0]["mesh"]["triangles"][12].reverse()
                    corrupted[0]["mesh"]["corner_normals"][12].reverse()
                else:
                    n=corrupted[0]["mesh"]["corner_normals"][12][0]
                    corrupted[0]["mesh"]["corner_normals"][12][0]=[-x for x in n]
                with self.assertRaisesRegex(ReceiverError,"vertex_winding_or_normal_changed"):
                    compare_reimported_glb(original,edited,corrupted)

    def test_duplicate_triangle_cannot_replace_another_source_triangle(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        corrupted=copy.deepcopy(edited)
        # Use adjacent fan faces whose vertices also occur in other faces;
        # the duplicate must reach the surface matcher with no orphan vertex.
        for key in ("triangles","corner_normals","corner_uv0"):
            corrupted[0]["mesh"][key][66]=copy.deepcopy(corrupted[0]["mesh"][key][65])
        self.assertEqual(set(range(corrupted[0]["vertices"])),
                         {index for face in corrupted[0]["mesh"]["triangles"] for index in face})
        with self.assertRaisesRegex(ReceiverError,"vertex_winding_or_normal_changed|multiplicity"):
            compare_reimported_glb(original,edited,corrupted)

    def test_rotation_and_scale_loss_are_rejected_even_with_equal_bounds(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        for field in ("rotation","scale"):
            with self.subTest(field=field):
                corrupted=copy.deepcopy(edited)
                matrix=corrupted[0]["matrix_world"]
                if field=="rotation":
                    angle=2*math.pi/32
                    matrix[0][0]=matrix[1][1]=math.cos(angle)
                    matrix[0][1]=-math.sin(angle)
                    matrix[1][0]=math.sin(angle)
                else:
                    matrix[0][0]=1.01
                with self.assertRaisesRegex(ReceiverError,"world_matrix_changed"):
                    compare_reimported_glb(original,edited,corrupted)

    def test_pbr_roughness_metallic_and_alpha_cannot_change_under_same_color(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        for name,value in (("Roughness",.9),("Metallic",1.),("Alpha",.5)):
            with self.subTest(name=name):
                corrupted=copy.deepcopy(edited)
                corrupted[0]["material"]["shader_inputs"][name]=value
                with self.assertRaisesRegex(ReceiverError,"shader_input_value_changed:"+name+"|requires_opaque_material"):
                    compare_reimported_glb(original,edited,corrupted)

    def test_snapshot_reads_real_mesh_matrix_and_shader_properties(self):
        bpy=fake_scene()
        before=source_scene_snapshot(bpy)
        focus=bpy.context.scene.objects[0]
        focus.location[0]+=EDIT_DELTA_X
        focus.matrix_world[0][3]+=EDIT_DELTA_X
        after=source_scene_snapshot(bpy)
        compare_edit(before,after)
        mutations=(
            lambda o: o.data.vertices[12].co.__setitem__(0,.013),
            lambda o: o.data.polygons[12].vertices.reverse(),
            lambda o: o.matrix_world[0].__setitem__(0,.95),
            lambda o: setattr(o.active_material.node_tree.nodes[0].inputs["Roughness"],
                              "default_value",.9),
            lambda o: setattr(o.active_material.node_tree.nodes[0].inputs["Metallic"],
                              "default_value",1.),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed=fake_scene(move_focus(snapshot()))
                mutation(changed.context.scene.objects[0])
                measured=source_scene_snapshot(changed)
                with self.assertRaises(ReceiverError):
                    compare_edit(before,measured)

    def test_linked_shader_or_wrong_active_output_cannot_hide_real_material_change(self):
        for change in ("input","output"):
            with self.subTest(change=change):
                bpy=fake_scene()
                nodes=bpy.context.scene.objects[0].active_material.node_tree.nodes
                if change=="input":
                    nodes[0].inputs["Base Color"].is_linked=True
                else:
                    nodes[1].inputs["Surface"].links=[]
                with self.assertRaisesRegex(ReceiverError,"shader_input|surface_not_bound"):
                    source_scene_snapshot(bpy)

    def test_mesh_indices_and_nonfinite_matrix_are_bounded(self):
        for change in ("index","nan","normal"):
            with self.subTest(change=change):
                value=snapshot()
                if change=="index":value[0]["mesh"]["triangles"][0][0]=999999
                elif change=="nan":value[0]["matrix_world"][0][0]=float("nan")
                else:value[0]["mesh"]["corner_normals"][0][0]=[0.,0.,0.]
                with self.assertRaises(ReceiverError):
                    bounded_source_snapshot(value)

    def _final_phase_fixture(self, label):
        """Five-phase receipt contract, with explicitly fake private file bytes."""
        dest=self.root/label
        dest.mkdir()
        info=original_glb(self.original,digest(self.original.read_bytes()))
        artifacts={}
        for name in ("original.blend","edited.blend","edited.glb","reimport.blend"):
            value=("CONTRACT-ONLY-FAKE-NATIVE-BYTES:"+name).encode()
            (dest/name).write_bytes(value)
            artifacts[name]={"name":name,"bytes":len(value),"sha256":digest(value)}
        before=snapshot()
        after=move_focus(copy.deepcopy(before))
        def row(phase,**values):
            return {"schema":SCHEMA,"phase":phase,"source":copy.deepcopy(info),**values}
        receipts={
            "import.json":row("imported_first_party_GLB_to_Blend",
                native_saved_file=artifacts["original.blend"],objects=before),
            "edit.json":row("original_Blend_reopened_and_edited",
                original_blend=artifacts["original.blend"],
                artifacts={"edited_blend":artifacts["edited.blend"],
                           "edited_glb":artifacts["edited.glb"]},objects=after),
            "verified.json":row("edited_Blend_reopened_in_new_Blender_process",
                original_native_blend=artifacts["original.blend"],
                edited_native_blend=artifacts["edited.blend"],
                edited_interchange_glb=artifacts["edited.glb"],
                original_objects=before,verified_edited_objects=after),
            "reimport.json":row("edited_GLTF_reimported_into_new_Blend",
                original_exported_glb=artifacts["edited.glb"],
                reimported_native_blend=artifacts["reimport.blend"],objects=after),
        }
        bpy=fake_scene(after)
        bpy.app=SimpleNamespace(version=(4,5,14),version_string="4.5.14")
        bpy.data=SimpleNamespace(filepath=str(dest/"reimport.blend"))
        args=SimpleNamespace(phase="reopen-reimport",root=str(dest),
                             source=str(self.original),source_sha256=info["sha256"])
        return dest,receipts,bpy,args

    def _invoke_final_contract(self, receipts, bpy, args):
        for name,row in receipts.items():
            write_receipt(pathlib.Path(args.root)/name,row)
        with mock.patch.dict(sys.modules,{"bpy":bpy}):
            receiver.run_phase(args)

    def test_final_phase_requires_each_consumed_receipt_source_phase_and_schema(self):
        for name in ("import.json","edit.json","verified.json","reimport.json"):
            for field in ("schema","phase","source"):
                with self.subTest(name=name,field=field):
                    dest,receipts,bpy,args=self._final_phase_fixture(name+"-"+field)
                    if field=="source":receipts[name]["source"]["sha256"]="f"*64
                    else:receipts[name][field]="foreign-value"
                    with self.assertRaisesRegex(ReceiverError,"receipt_chain_not_source_bound"):
                        self._invoke_final_contract(receipts,bpy,args)
                    self.assertFalse((dest/"complete.json").exists())

    def test_final_phase_binds_all_original_and_edited_artifact_bytes(self):
        for name in ("original.blend","edited.blend","edited.glb"):
            with self.subTest(name=name):
                dest,receipts,bpy,args=self._final_phase_fixture(name+"-tampered")
                (dest/name).write_bytes(b"KNOWN-CONTRACT-FILE-CONTENT-CHANGE")
                with self.assertRaisesRegex(ReceiverError,"original_or_edited_artifact_changed"):
                    self._invoke_final_contract(receipts,bpy,args)
                self.assertFalse((dest/"complete.json").exists())

    def test_final_phase_rejects_changed_cross_receipt_artifact_join(self):
        dest,receipts,bpy,args=self._final_phase_fixture("artifact-join")
        receipts["verified.json"]["edited_interchange_glb"]=dict(
            receipts["verified.json"]["edited_interchange_glb"],sha256="e"*64)
        with self.assertRaisesRegex(ReceiverError,"artifact_or_snapshot_join_changed"):
            self._invoke_final_contract(receipts,bpy,args)
        self.assertFalse((dest/"complete.json").exists())

    def test_final_phase_has_stable_positive_contract_without_original_project_claim(self):
        dest,receipts,bpy,args=self._final_phase_fixture("positive")
        self._invoke_final_contract(receipts,bpy,args)
        result=read_json(dest/"complete.json")
        self.assertFalse(result["original_Motionwright_native_blend_source_proven"])
        self.assertFalse(result["original_Studio_UUID_binding_proven"])
        self.assertEqual(result["original_Semwright_Broker_jobs_started"],0)

    def test_final_phase_cannot_use_matching_snapshots_missing_new_measurements(self):
        dest,receipts,bpy,args=self._final_phase_fixture("legacy-measurements")
        del receipts["import.json"]["objects"][0]["content_snapshot_version"]
        with self.assertRaisesRegex(ReceiverError,"missing_full_content_measurement"):
            self._invoke_final_contract(receipts,bpy,args)
        self.assertFalse((dest/"complete.json").exists())

    def test_hidden_objects_and_ancestor_collections_are_refused(self):
        mutations=(
            lambda b:setattr(b.context.scene.objects[0],"hide_render",True),
            lambda b:setattr(b.context.scene.objects[0],"hide_viewport",True),
            lambda b:setattr(b.context.scene.objects[0],"hide_get",lambda:True),
            lambda b:setattr(b.context.scene.objects[0],"visible_get",lambda:False),
            lambda b:setattr(b.context.scene.collection,"hide_render",True),
            lambda b:b.context.scene.collection.children.append(
                SimpleNamespace(hide_render=True,hide_viewport=False,children=[])),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                bpy=fake_scene();mutation(bpy)
                with self.assertRaisesRegex(ReceiverError,"visibility"):
                    source_scene_snapshot(bpy)

    def test_material_render_flags_must_match_opaque_profile_and_types(self):
        settings=snapshot()[0]["material"]["render_settings"]
        for key,value in settings.items():
            with self.subTest(key=key):
                bpy=fake_scene()
                setattr(bpy.context.scene.objects[0].active_material,key,
                        not value if type(value) is bool else "BLENDED")
                with self.assertRaisesRegex(ReceiverError,"material_render_settings"):
                    source_scene_snapshot(bpy)
                receipt=snapshot()
                receipt[0]["material"]["render_settings"][key]=(
                    int(value) if type(value) is bool else False)
                with self.assertRaisesRegex(ReceiverError,"material_render_settings"):
                    bounded_source_snapshot(receipt)

    def test_animated_material_data_and_engine_specific_output_are_refused(self):
        for change in ("material","node_tree","data","scene","output"):
            with self.subTest(change=change):
                bpy=fake_scene();obj=bpy.context.scene.objects[0]
                target={"material":obj.active_material,
                        "node_tree":obj.active_material.node_tree,
                        "data":obj.data,"scene":bpy.context.scene}.get(change)
                if change=="output":obj.active_material.node_tree.nodes[1].target="CYCLES"
                else:target.animation_data=SimpleNamespace()
                with self.assertRaisesRegex(ReceiverError,"animation|material_graph"):
                    source_scene_snapshot(bpy)

    def test_original_glb_opacity_and_double_sided_policy_are_explicit(self):
        for key,value in (("alphaMode","BLEND"),("alphaMode","MASK"),
                          ("doubleSided",False),("doubleSided",1)):
            with self.subTest(key=key,value=value):
                materials=[{"name":name+"-material","doubleSided":True,
                            "pbrMetallicRoughness":{"baseColorFactor":[.2,.3,.4,1.]}}
                           for name in (PANEL,FOCUS)]
                materials[0][key]=value
                path=self.root/("material-"+key+"-"+str(value)+".glb")
                path.write_bytes(original_two_object_glb(materials=materials))
                with self.assertRaisesRegex(ReceiverError,"opacity_or_culling"):
                    original_glb(path,digest(path.read_bytes()))

    def test_uv_change_without_geometry_or_material_change_is_rejected(self):
        original=snapshot()
        edited=move_focus(copy.deepcopy(original))
        changed=copy.deepcopy(edited)
        changed[0]["mesh"]["corner_uv0"][12][0][0]+=.04
        with self.assertRaisesRegex(ReceiverError,"vertex_winding_or_normal_changed"):
            compare_reimported_glb(original,edited,changed)
        bpy=fake_scene(edited)
        bpy.context.scene.objects[0].data.uv_layers.active.data[36].uv[0]+=.04
        actual=source_scene_snapshot(bpy)
        with self.assertRaisesRegex(ReceiverError,"unapproved_semantic_change:mesh"):
            compare_edit(original,actual)

    def test_missing_or_unbounded_uv_measurement_cannot_pass(self):
        for change in ("missing","nan","wrong-size"):
            with self.subTest(change=change):
                value=snapshot()
                if change=="missing":del value[0]["mesh"]["corner_uv0"]
                elif change=="nan":value[0]["mesh"]["corner_uv0"][0][0][0]=float("nan")
                else:value[0]["mesh"]["corner_uv0"][0].pop()
                with self.assertRaisesRegex(ReceiverError,"UV0_coordinates"):
                    bounded_source_snapshot(value)
        bpy=fake_scene()
        bpy.context.scene.objects[0].data.uv_layers.clear()
        with self.assertRaisesRegex(ReceiverError,"UV_layer_missing"):
            source_scene_snapshot(bpy)

    def test_unreferenced_editable_vertices_are_not_silently_lost_by_surface_matching(self):
        value=snapshot()
        value[0]["mesh"]["positions"].append([.11,.22,.33])
        value[0]["vertices"]+=1
        with self.assertRaisesRegex(ReceiverError,"unreferenced_editable_vertex"):
            bounded_source_snapshot(value)

    def test_final_phase_malformed_receipt_shape_fails_closed(self):
        for change in ("missing-artifacts","null-artifacts","missing-snapshot"):
            with self.subTest(change=change):
                dest,receipts,bpy,args=self._final_phase_fixture(change)
                if change=="missing-artifacts":del receipts["edit.json"]["artifacts"]
                elif change=="null-artifacts":receipts["edit.json"]["artifacts"]=None
                else:del receipts["import.json"]["objects"]
                with self.assertRaisesRegex(ReceiverError,"artifact_or_snapshot_shape"):
                    self._invoke_final_contract(receipts,bpy,args)
                self.assertFalse((dest/"complete.json").exists())



if __name__=="__main__":
    unittest.main()
