"""Offline negative/positive strict glTF binary geometry tests, no Blender execution."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from native_blender_glb_content import (
    BlenderContentError, check_glb_source_geometry
)

SOURCE = [
 {"source_name":"Motionwright panel","kind":"rectangle",
  "x":260.,"y":180.,"width":520.,"height":260.,"z_index":1,
  "fill_hex":"#4F7CAC","node_id":"abcdef012345"+"0"*20},
 {"source_name":"Motionwright focus","kind":"circle",
  "x":920.,"y":360.,"width":220.,"height":220.,"z_index":1,
  "fill_hex":"#F5B84A","node_id":"0123456789ab"+"0"*20}
]
def sha(b):
    return hashlib.sha256(b).hexdigest()
def make_case(*, full_uuid=True):
    # This is an inert first-party *unit-test* geometry, not an actual Blender
    # rendering. Only native provider artifact evidence can certify Blender.
    payload=bytearray()
    buffer_views=[]
    accessors=[]
    def add(values,shape,component):
        nonlocal payload
        count=len(values)
        before=len(payload)
        char={5126:"f",5123:"H"}[component]
        arity={"VEC3":3,"VEC2":2,"SCALAR":1}[shape]
        for row in values:
            if arity==1: row=(row,)
            payload.extend(struct.pack("<"+char*arity,*row))
        length=len(payload)-before
        while len(payload)%4:payload.append(0)
        buffer_views.append({"buffer":0,"byteOffset":before,"byteLength":length})
        item={"bufferView":len(buffer_views)-1,
              "componentType":component,"count":count,"type":shape}
        if shape=="VEC3" and component==5126 and (count==4 or count==192):
            item["min"]=[min(v[i] for v in values) for i in range(3)]
            item["max"]=[max(v[i] for v in values) for i in range(3)]
        accessors.append(item)
        return len(accessors)-1
    plane=[(-1.,0.,-1.),(1.,0.,-1.),(1.,0.,1.),(-1.,0.,1.)]
    plane_n=[(0.,1.,0.)]*4
    plane_uv=[(0.,0.),(1.,0.),(1.,1.),(0.,1.)]
    ring_top=[(math.cos(2*math.pi*i/32),1.,
               math.sin(2*math.pi*i/32)) for i in range(32)]
    ring_bottom=[(x,-1.,z) for x,_,z in ring_top]
    # Every vertex is used. Keep the flat side corners separate from the cap
    # corners so a valid outward normal and nondegenerate UV exist per face.
    cylinder=[]
    cylinder_n=[]
    cylinder_uv=[]
    cylinder_indices=[]
    for i in range(32):
        j=(i+1)%32
        base=len(cylinder)
        cylinder.extend((ring_top[i],ring_bottom[i],ring_top[j],ring_bottom[j]))
        angle=2*math.pi*(i+.5)/32
        cylinder_n.extend([(math.cos(angle),0.,math.sin(angle))]*4)
        cylinder_uv.extend(((i/32,0.),(i/32,1.),((i+1)/32,0.),((i+1)/32,1.)))
        cylinder_indices.extend((base,base+2,base+1,base+2,base+3,base+1))
    for ring,normal,reverse in ((ring_top,(0.,1.,0.),True),
                                (ring_bottom,(0.,-1.,0.),False)):
        base=len(cylinder)
        cylinder.extend(ring)
        cylinder_n.extend([normal]*32)
        cylinder_uv.extend(((x+1)/2,(z+1)/2) for x,_,z in ring)
        for i in range(1,31):
            cylinder_indices.extend((base,base+i+1,base+i) if reverse
                                    else (base,base+i,base+i+1))
    assert len(cylinder)==192 and len(cylinder_indices)==372
    ppos=add(plane,"VEC3",5126)
    pnorm=add(plane_n,"VEC3",5126)
    puv=add(plane_uv,"VEC2",5126)
    pind=add([0,2,1,0,3,2],"SCALAR",5123)
    cpos=add(cylinder,"VEC3",5126)
    cnorm=add(cylinder_n,"VEC3",5126)
    cuv=add(cylinder_uv,"VEC2",5126)
    cind=add(cylinder_indices,"SCALAR",5123)
    gltf={
       "asset":{"version":"2.0","generator":"synthetic offline test, not Blender"},
       "scene":0,"scenes":[{"nodes":[0,1]}],
       "buffers":[{"byteLength":len(payload)}],
       "bufferViews":buffer_views,
       "accessors":accessors,
       "nodes":[
          {"name":"Motionwright-panel-abcdef012345"+("0"*20 if full_uuid else ""),
           "mesh":0,"translation":[-4.4,.01,-2.3],"scale":[2.6,1.,1.3]},
          {"name":"Motionwright-focus-0123456789ab"+("0"*20 if full_uuid else ""),
           "mesh":1,"translation":[.7,.01,-.7],"scale":[1.1,.005,1.1]}],
       "meshes":[
          {"name":"Plane","primitives":[
             {"attributes":{"POSITION":ppos,"NORMAL":pnorm,"TEXCOORD_0":puv},
              "indices":pind,"material":0}]},
          {"name":"Cylinder","primitives":[
             {"attributes":{"POSITION":cpos,"NORMAL":cnorm,"TEXCOORD_0":cuv},
              "indices":cind,"material":1}]}],
       "materials":[]
    }
    for node, fill in zip(gltf["nodes"],("#4F7CAC","#F5B84A")):
        color=[int(fill[j:j+2],16)/255 for j in (1,3,5)]+[1.]
        gltf["materials"].append({
            "name":node["name"]+"-material","doubleSided":True,
            "pbrMetallicRoughness":{
                "baseColorFactor":color,"metallicFactor":0.,
                "roughnessFactor":.45
            }
        })
    return gltf,bytes(payload)

def blob(gltf, data):
    j=json.dumps(gltf,separators=(",",":"),allow_nan=False).encode("utf8")
    j+=bytes([32])*((-len(j))%4)
    data+=bytes([0])*((-len(data))%4)
    return struct.pack("<4sII",b"glTF",2,12+8+len(j)+8+len(data))+(
        struct.pack("<II",len(j),0x4e4f534a)+j+
        struct.pack("<II",len(data),0x004e4942)+data)
def check(gltf,binary,source=None):
    encoded=blob(gltf,binary)
    return check_glb_source_geometry(encoded,
        original_GLB_SHA256=sha(encoded),
        source_nodes=copy.deepcopy(SOURCE if source is None else source))

def accessor_offset(gltf,index):
    entry=gltf["accessors"][index]
    view=gltf["bufferViews"][entry["bufferView"]]
    return view.get("byteOffset",0)+entry.get("byteOffset",0)

def rewrite_indices(gltf,binary,mesh_index,indices):
    result=bytearray(binary)
    index=gltf["meshes"][mesh_index]["primitives"][0]["indices"]
    struct.pack_into("<"+"H"*len(indices),result,accessor_offset(gltf,index),*indices)
    return bytes(result)

class NativeBlenderGlbContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gltf,cls.binary=make_case()

    def customized(self,transform,source=None):
        g=copy.deepcopy(self.gltf)
        b=bytes(self.binary)
        transform(g)
        return check(g,b,source)

    def test_source_bound_binary_plane_and_cylinder_pass(self):
        result=check(self.gltf,self.binary)
        self.assertEqual(result["object_count"],2)
        self.assertTrue(result["actual_binary_geometry_and_world_bounds_verified"])
        self.assertTrue(result["source_Project_UUIDs_explicitly_checked"])
        self.assertFalse(result["native_blend_source_reopen_executed"])
        self.assertEqual(
            {(o["source_name"],o["vertices"],o["triangles"])
             for o in result["object_reports"]},
            {("Motionwright panel",4,2),("Motionwright focus",192,124)})
    def test_older_complete_UUID_in_native_name_is_admitted_when_source_bound(self):
        g,b=make_case(full_uuid=True)
        self.assertTrue(check(g,b)["source_Project_UUIDs_explicitly_checked"])
    def test_legacy_short_uuid_cannot_claim_exact_identity_even_with_authorized_source(self):
        g,b=make_case(full_uuid=False)
        with self.assertRaisesRegex(BlenderContentError,"node_id_not_retained"):
            check(g,b)
        source=copy.deepcopy(SOURCE)
        source[0]["node_id"]=source[0]["node_id"][:12]+"f"*20
        with self.assertRaisesRegex(BlenderContentError,"node_id_not_retained"):
            check(g,b,source)
        for row in source:row.pop("node_id")
        result=check(g,b,source)
        self.assertFalse(result["source_Project_UUIDs_explicitly_checked"])
        self.assertTrue(all(not row["source_id_exactly_bound"]
                            for row in result["object_reports"]))
    def test_externally_unbound_UUID_is_not_confused_with_original_project_identity(self):
        source=copy.deepcopy(SOURCE)
        source[0].pop("node_id")
        source[1].pop("node_id")
        self.assertFalse(check(self.gltf,self.binary,source)[
            "source_Project_UUIDs_explicitly_checked"])
        source[1]["node_id"]="eeeeeeeeeeee"+"0"*20
        with self.assertRaisesRegex(BlenderContentError,"node_id_not_retained"):
            check(self.gltf,self.binary,source)

    def test_wrong_geometry_position_or_depth_fails(self):
        for index,component in ((0,0),(1,1)):
            with self.subTest(index=index,component=component):
                def alter(g):
                    g["nodes"][index]["translation"][component]+=.12
                with self.assertRaisesRegex(BlenderContentError,"shape_transform"):
                    self.customized(alter)
    def test_wrong_scale_fails_despite_mesh_and_color(self):
        with self.assertRaisesRegex(BlenderContentError,"shape_transform"):
            self.customized(lambda g:g["nodes"][0]["scale"].__setitem__(0,1.3))
    def test_material_color_changes_fail_even_when_indices_unchanged(self):
        def mutate(g):g["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"][0]=.0
        with self.assertRaisesRegex(BlenderContentError,"PBR_changed"):
            self.customized(mutate)
    def test_wrong_material_binding_cannot_swap_styles(self):
        def mutate(g):g["meshes"][0]["primitives"][0]["material"]=1
        with self.assertRaisesRegex(BlenderContentError,"material_identity"):
            self.customized(mutate)
    def test_missing_explicit_nonmetallic_material_is_rejected(self):
        with self.assertRaisesRegex(BlenderContentError,"PBR_changed"):
            self.customized(lambda g:g["materials"][0]["pbrMetallicRoughness"].pop("metallicFactor"))
    def test_scene_roots_are_exact_unique_integer_indices(self):
        for roots in ([0,0,1],[False,1],[0],[-1,1]):
            with self.subTest(roots=roots):
                with self.assertRaisesRegex(BlenderContentError,"scene_hierarchy"):
                    self.customized(lambda g:g["scenes"][0].__setitem__("nodes",roots))
    def test_shrunk_or_missing_mesh_indices_fail(self):
        def mutate(g):g["accessors"][7]["count"]-=3
        with self.assertRaisesRegex(BlenderContentError,"triangle_count_changed"):
            self.customized(mutate)
    def test_external_binary_reference_is_not_silent_import(self):
        def mutate(g):g["buffers"][0]["uri"]="https://example.invalid/source.bin"
        with self.assertRaisesRegex(BlenderContentError,"external_or_multiple_GLTF_buffers"):
            self.customized(mutate)
    def test_raw_truncation_and_stale_digest_fail(self):
        payload=blob(self.gltf,self.binary)
        with self.assertRaisesRegex(BlenderContentError,"custody_SHA256"):
            check_glb_source_geometry(payload,original_GLB_SHA256="0"*64,source_nodes=SOURCE)
        with self.assertRaisesRegex(BlenderContentError,"GLB_header_or_length"):
            check_glb_source_geometry(payload[:-4],
                original_GLB_SHA256=sha(payload[:-4]),source_nodes=SOURCE)
    def test_original_plan_wrong_color_does_not_validate_mesh(self):
        source=copy.deepcopy(SOURCE)
        source[0]["fill_hex"]="#FF0000"
        with self.assertRaisesRegex(BlenderContentError,"PBR_changed"):
            check(self.gltf,self.binary,source)
    def test_unapproved_animation_or_extra_object_fails(self):
        with self.assertRaisesRegex(BlenderContentError,"unadmitted_GLB_component"):
            self.customized(lambda g:g.__setitem__("animations",[{}]))
        with self.assertRaisesRegex(BlenderContentError,"scene_hierarchy"):
            self.customized(lambda g:g["nodes"].append(copy.deepcopy(g["nodes"][0])))
    def test_extra_or_nonfinite_source_controls_fail(self):
        source=copy.deepcopy(SOURCE)
        source[0]["x"]=float("nan")
        with self.assertRaisesRegex(BlenderContentError,"invalid_authored_geometry"):
            check(self.gltf,self.binary,source)
        source=copy.deepcopy(SOURCE)
        source[1]["width"]+=2
        with self.assertRaisesRegex(BlenderContentError,"unadmitted_ellipse"):
            check(self.gltf,self.binary,source)
    def test_position_accessor_reports_cannot_lie_about_binary_content(self):
        def mutate(g):g["accessors"][0]["max"]=[1,0,50]
        with self.assertRaisesRegex(BlenderContentError,"position_min_max"):
            self.customized(mutate)
    def test_invalid_indices_and_degenerate_triangles_fail(self):
        # Corrupt actual binary indices; don't merely mutate JSON descriptions.
        invalid=bytearray(self.binary)
        view=self.gltf["bufferViews"][3]
        struct.pack_into("<H",invalid,view["byteOffset"],512)
        with self.assertRaisesRegex(BlenderContentError,"triangle_index"):
            check(self.gltf,bytes(invalid))
        invalid=bytearray(self.binary)
        struct.pack_into("<HHH",invalid,view["byteOffset"],0,0,0)
        with self.assertRaisesRegex(BlenderContentError,"zero_area"):
            check(self.gltf,bytes(invalid))

    def test_repeated_plane_triangle_cannot_stand_for_both_halves(self):
        raw=rewrite_indices(self.gltf,self.binary,0,[0,2,1,0,2,1])
        with self.assertRaisesRegex(BlenderContentError,"duplicate_surface_triangle"):
            check(self.gltf,raw)

    def test_distinct_overlapping_plane_triangles_do_not_cover_the_plane(self):
        raw=rewrite_indices(self.gltf,self.binary,0,[0,2,1,0,3,1])
        with self.assertRaisesRegex(BlenderContentError,"surface_boundary"):
            check(self.gltf,raw)

    def test_repeating_one_cylinder_triangle_124_times_is_rejected(self):
        raw=rewrite_indices(self.gltf,self.binary,1,[0,2,1]*124)
        with self.assertRaisesRegex(BlenderContentError,"duplicate_surface_triangle"):
            check(self.gltf,raw)

    def test_unique_cylinder_cap_triangles_must_cover_the_whole_cap(self):
        index=self.gltf["meshes"][1]["primitives"][0]["indices"]
        values=list(struct.unpack_from("<372H",self.binary,accessor_offset(self.gltf,index)))
        # Replace a cap triangle by a different, nondegenerate triangle. All
        # bounds/counts remain valid, and there are no duplicate triangles.
        values[192:195]=[129,131,130]
        raw=rewrite_indices(self.gltf,self.binary,1,values)
        with self.assertRaisesRegex(BlenderContentError,"surface_boundary"):
            check(self.gltf,raw)

    def test_cylinder_side_triangle_cannot_jump_over_a_panel(self):
        raw=rewrite_indices(self.gltf,self.binary,1,[0,6,1])
        with self.assertRaisesRegex(BlenderContentError,"cylinder_side_panel"):
            check(self.gltf,raw)

    def test_negative_and_out_of_range_materials_fail_with_content_error(self):
        for mesh_index,material in ((0,-2),(1,-1),(0,2),(0,True)):
            with self.subTest(mesh_index=mesh_index,material=material):
                def mutate(g):g["meshes"][mesh_index]["primitives"][0]["material"]=material
                with self.assertRaisesRegex(BlenderContentError,"material_index"):
                    self.customized(mutate)

    def test_outward_normals_are_required_for_the_original_surface(self):
        raw=bytearray(self.binary)
        index=self.gltf["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]
        struct.pack_into("<12f",raw,accessor_offset(self.gltf,index),*([0.,-1.,0.]*4))
        with self.assertRaisesRegex(BlenderContentError,"surface_normals"):
            check(self.gltf,bytes(raw))

    def test_collapsed_uv_triangles_are_not_reported_as_preserved(self):
        raw=bytearray(self.binary)
        index=self.gltf["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]
        struct.pack_into("<8f",raw,accessor_offset(self.gltf,index),*([0.,0.]*4))
        with self.assertRaisesRegex(BlenderContentError,"degenerate_UV_triangle"):
            check(self.gltf,bytes(raw))

    def test_additional_emission_is_not_the_original_authored_material(self):
        with self.assertRaisesRegex(BlenderContentError,"source_material_emission"):
            self.customized(lambda g:g["materials"][0].__setitem__("emissiveFactor",[1.,0.,0.]))
        result=self.customized(lambda g:g["materials"][0].__setitem__("emissiveFactor",[0.,0.,0.]))
        self.assertEqual(result["object_count"],2)

if __name__=="__main__":
    unittest.main()
