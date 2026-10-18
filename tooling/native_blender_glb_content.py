"""Read-only Blender 4.5.14 Plane/Cylinder GLB geometry/content admission.

This compares fully decoded binary geometry/materials/axis transforms with an
explicit caller-supplied original Motionwright CanvasNode source projection.
It does NOT run a Blender driver, reopen a .blend file, grant permissions,
verify external rights, or certify an installed Studio/Blender edit round-trip.

The GLB's abbreviated node ID is not trusted as exact Project identity unless
the caller separately supplies an exact original node_id from an authorized
source snapshot. The GLB's own JSON cannot establish original Project authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct

SCHEMA = "motionwright.native.blender-glb-source-pixel-bounds/1"
MAX_GLB_BYTES = 8 * 1024 * 1024
MAX_MODEL_OBJECTS = 16
COMPONENTS = {5126: ("f", 4), 5123: ("H", 2), 5125: ("I", 4)}
ELEMENTS = {"VEC2": 2, "VEC3": 3, "SCALAR": 1}
SHAPES = {
    "rectangle": {"mesh_name": "Plane", "vertices": 4, "indices": 6},
    "circle": {"mesh_name": "Cylinder", "vertices": 192, "indices": 372},
}

class BlenderContentError(ValueError):
    """Fail closed on missing, malformed or semantically wrong GLB content."""

def need(ok, why):
    if not ok:
        raise BlenderContentError(why)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def no_duplicates(pairs):
    output = {}
    for key, value in pairs:
        need(key not in output, "duplicate_gltf_json_field")
        output[key] = value
    return output

def strict_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=no_duplicates,
            parse_constant=lambda name: (_ for _ in ()).throw(
                BlenderContentError("nonfinite_gltf_json")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BlenderContentError("unparseable_gltf_json") from exc

def unpack_glb(blob: bytes, expected_sha256: str):
    need(isinstance(blob, bytes) and 80 <= len(blob) <= MAX_GLB_BYTES,
         "bounded_GLB_blob_required")
    need(isinstance(expected_sha256, str) and
         re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None,
         "original_GLB_sha256_required")
    need(sha(blob) == expected_sha256, "GLB_custody_SHA256_mismatch")
    magic, version, length = struct.unpack_from("<4sII", blob)
    need((magic, version, length) == (b"glTF", 2, len(blob)),
         "GLB_header_or_length_mismatch")
    at = 12
    chunks = []
    while at < len(blob):
        need(at+8 <= len(blob), "truncated_GLB_chunk_header")
        size, chunk_type = struct.unpack_from("<II", blob, at)
        at += 8
        need(size>0 and size%4==0 and at+size<=len(blob),
             "truncated_or_unaligned_GLB_chunk")
        chunks.append((chunk_type, blob[at:at+size]))
        at += size
    need(len(chunks)==2 and chunks[0][0]==0x4E4F534A and
         chunks[1][0]==0x004E4942, "expected_single_JSON_and_BIN_GLB")
    gltf = strict_json(chunks[0][1].decode("utf8").rstrip(" \x00"))
    need(type(gltf) is dict and
         type(gltf.get("asset")) is dict and
         gltf["asset"].get("version")=="2.0",
         "not_gltf2")
    need(not gltf.get("extensionsUsed") and not gltf.get("extensionsRequired"),
         "unadmitted_GLB_extensions")
    for unexpected in ("animations", "skins", "cameras", "images", "textures"):
        need(not gltf.get(unexpected), "unadmitted_GLB_component:"+unexpected)
    need(len(gltf.get("buffers", []))==1 and
         gltf["buffers"][0].get("uri") is None,
         "external_or_multiple_GLTF_buffers")
    logical = gltf["buffers"][0].get("byteLength")
    need(type(logical) is int and
         logical>0 and len(chunks[1][1])-3<=logical<=len(chunks[1][1]),
         "GLB_binary_payload_size_mismatch")
    return gltf, chunks[1][1][:logical]

def numbers_equal(observed, required, *, tolerance=1e-4):
    if not isinstance(observed, (list, tuple)) or len(observed)!=len(required):
        return False
    return all(type(v) in (int, float) and math.isfinite(v) and
               abs(v-expected)<=tolerance
               for v, expected in zip(observed, required))

def accessor(gltf: dict, binary: bytes, index: int, kind: str, component: int):
    entries = gltf.get("accessors", [])
    views = gltf.get("bufferViews", [])
    need(type(index) is int and 0<=index<len(entries),
         "invalid_mesh_accessor_index")
    entry = entries[index]
    need(type(entry) is dict and entry.get("type")==kind and
         entry.get("componentType")==component and
         not entry.get("sparse") and entry.get("normalized") in (None, False),
         "unadmitted_mesh_accessor_type")
    count = entry.get("count")
    view_index = entry.get("bufferView")
    need(type(count) is int and 1<=count<=100_000 and
         type(view_index) is int and 0<=view_index<len(views),
         "unbounded_or_missing_mesh_accessor")
    view = views[view_index]
    need(type(view) is dict and view.get("buffer")==0,
         "foreign_mesh_accessor_buffer")
    fmt, byte_unit = COMPONENTS[component]
    width = ELEMENTS[kind]
    stride = view.get("byteStride", width*byte_unit)
    offset = view.get("byteOffset", 0)
    local = entry.get("byteOffset", 0)
    length = view.get("byteLength")
    need(all(type(x) is int for x in (stride,offset,local,length)) and
         offset>=0 and local>=0 and length>0 and
         stride>=width*byte_unit and stride%byte_unit==0 and
         local+(count-1)*stride+width*byte_unit<=length and
         offset+length<=len(binary),
         "mesh_accessor_binary_bounds")
    output=[]
    for i in range(count):
        values=struct.unpack_from("<"+fmt*width, binary, offset+local+i*stride)
        need(all(math.isfinite(value) for value in values),
             "nonfinite_mesh_binary_value")
        output.append(values[0] if width==1 else values)
    return output, entry

def validate_plan(source_nodes):
    need(type(source_nodes) is list and
         1<=len(source_nodes)<=MAX_MODEL_OBJECTS,
         "bounded_authoritative_source_nodes_required")
    for node in source_nodes:
        need(type(node) is dict and node.get("kind") in SHAPES and
             type(node.get("source_name")) is str and
             1<=len(node["source_name"])<=63 and
             re.fullmatch(r"#[0-9A-Fa-f]{6}", str(node.get("fill_hex"))) is not None,
             "unsupported_source_shape_name_or_fill")
        for attr in ("x","y","width","height","z_index"):
            v=node.get(attr)
            need(type(v) in (float,int) and math.isfinite(v),
                 "invalid_authored_geometry:"+attr)
        need(node["width"]>0 and node["height"]>0 and
             0<=node["x"]<1920 and 0<=node["y"]<1080 and
             node["x"]+node["width"]<=1920 and
             node["y"]+node["height"]<=1080,
             "source_geometry_outside_canvas")
        need(float(node["z_index"]).is_integer(),
             "noninteger_authored_z_index")
        if node["kind"]=="circle":
            need(node["width"]==node["height"],
                 "unadmitted_ellipse_as_cylinder")
        if "node_id" in node:
            val=node["node_id"]
            need(type(val) is str and
                 re.fullmatch(r"[0-9a-fA-F-]{32,36}", val) is not None and
                 len(val.replace("-",""))==32,
                 "invalid_authoritative_source_node_id")
    need(len({n["source_name"] for n in source_nodes})==len(source_nodes),
         "duplicate_source_shape_identity")

def get_source_expected(node):
    cx=node["x"]+node["width"]/2
    cy=node["y"]+node["height"]/2
    translation=[(cx-960)*0.01,float(node["z_index"])*0.01,(cy-540)*0.01]
    scale=[node["width"]*.005,
           .005 if node["kind"]=="circle" else 1.0,
           node["height"]*.005]
    return translation, scale

def check_glb_source_geometry(glb_bytes: bytes, *, original_GLB_SHA256: str,
                              source_nodes: list[dict]) -> dict:
    """Check source-bound geometry, PBR appearance, index/triangle content."""
    validate_plan(source_nodes)
    gltf, binary = unpack_glb(glb_bytes,original_GLB_SHA256)
    scenes=gltf.get("scenes",[])
    nodes=gltf.get("nodes",[])
    meshes=gltf.get("meshes",[])
    materials=gltf.get("materials",[])
    need(len(scenes)==1 and gltf.get("scene",0)==0 and
         len(nodes)==len(meshes)==len(materials)==len(source_nodes) and
         set(scenes[0].get("nodes", []))==set(range(len(nodes))),
         "scene_hierarchy_or_content_count_differs_from_source")
    expected_sorted=sorted(source_nodes,key=lambda x:x["source_name"])
    actual_names=[]
    observed=[]
    for node_index, source in enumerate(expected_sorted):
        expected_label=re.sub(r"[^A-Za-z0-9]+","-",source["source_name"]).strip("-")
        candidates=[(i,n) for i,n in enumerate(nodes)
                    if re.fullmatch(re.escape(expected_label)+r"-(?:[0-9a-f]{12}|[0-9a-f]{32})",
                                    str(n.get("name","")))]
        need(len(candidates)==1,"native_semantic_object_name_missing_or_ambiguous")
        i, node=candidates[0]
        need(i not in actual_names,"source_blender_object_reused")
        actual_names.append(i)
        if "node_id" in source:
            full_id=source["node_id"].replace("-","").lower()
            actual_id=node["name"][len(expected_label)+1:]
            need(actual_id in (full_id,full_id[:12]),
                 "original_Project_node_id_not_retained")
        need(type(node.get("mesh")) is int and 0<=node["mesh"]<len(meshes) and
             not node.get("children") and not node.get("matrix"),
             "unexpected_node_hierarchy_or_transform_override")
        expected_location, expected_scale = get_source_expected(source)
        need(numbers_equal(node.get("translation", [0,0,0]),expected_location) and
             numbers_equal(node.get("scale", [1,1,1]),expected_scale) and
             numbers_equal(node.get("rotation", [0,0,0,1]),[0,0,0,1]),
             "source_shape_transform_changed:"+source["source_name"])
        mesh=meshes[node["mesh"]]
        desc=SHAPES[source["kind"]]
        need(mesh.get("name")==desc["mesh_name"] and
             type(mesh.get("primitives")) is list and len(mesh["primitives"])==1,
             "source_primitive_mesh_semantics_changed")
        prim=mesh["primitives"][0]
        need(prim.get("mode",4)==4 and type(prim.get("material")) is int and
             not prim.get("targets"),"unadmitted_render_primitive")
        material=materials[prim["material"]]
        need(material.get("name")==node["name"]+"-material" and
             material.get("doubleSided") is True and
             material.get("alphaMode","OPAQUE")=="OPAQUE",
             "source_material_identity_or_opacity_changed")
        pbr=material.get("pbrMetallicRoughness",{})
        h=source["fill_hex"][1:]
        color=[int(h[j:j+2],16)/255 for j in (0,2,4)]+[1.]
        need(numbers_equal(pbr.get("baseColorFactor",[1,1,1,1]),color,tolerance=1e-5)
             and abs(pbr.get("metallicFactor",0))<1e-6
             and abs(pbr.get("roughnessFactor",1)-.45)<1e-5,
             "source_material_color_or_PBR_changed")
        attrs=prim.get("attributes",{})
        need(set(attrs)=={"POSITION","NORMAL","TEXCOORD_0"} and
             type(prim.get("indices")) is int,
             "source_geometry_attributes_missing")
        positions,posmeta=accessor(gltf,binary,attrs["POSITION"],"VEC3",5126)
        normals,_=accessor(gltf,binary,attrs["NORMAL"],"VEC3",5126)
        texture,_=accessor(gltf,binary,attrs["TEXCOORD_0"],"VEC2",5126)
        ids,_=accessor(gltf,binary,prim["indices"],"SCALAR",5123)
        need(len(positions)==desc["vertices"] and len(ids)==desc["indices"] and
             len(normals)==len(texture)==len(positions) and len(ids)%3==0,
             "source_primitive_vertex_or_triangle_count_changed")
        need(all(0<=j<len(positions) for j in ids),
             "invalid_GLTF_triangle_index")
        bounds=[(min(v[axis] for v in positions),max(v[axis] for v in positions))
                for axis in range(3)]
        need(numbers_equal(posmeta.get("min"),[a for a,b in bounds]) and
             numbers_equal(posmeta.get("max"),[b for a,b in bounds]),
             "lying_GLTF_position_min_max")
        if source["kind"]=="rectangle":
            need(all(abs(x[1])<1e-5 for x in positions),
                 "Plane_vertex_outside_expected_local_plane")
        else:
            need(all(abs(math.hypot(x[0],x[2])-1)<.005 and
                     abs(abs(x[1])-1)<.005 for x in positions),
                 "Circle_cylinder_local_geometry_not_circular")
        need(all(.85<math.sqrt(sum(c*c for c in x))<1.15 for x in normals),
             "GLTF_normals_not_unit")
        # Actual geometry and its vertex/connectivity—not just JSON min/max.
        smallest_area=math.inf
        for tri in range(0,len(ids),3):
            pa,pb,pc=(positions[ids[k]] for k in range(tri,tri+3))
            u=[pb[j]-pa[j] for j in range(3)]
            v=[pc[j]-pa[j] for j in range(3)]
            cross=[u[1]*v[2]-u[2]*v[1],
                   u[2]*v[0]-u[0]*v[2],
                   u[0]*v[1]-u[1]*v[0]]
            area=.5*math.sqrt(sum(z*z for z in cross))
            smallest_area=min(smallest_area,area)
        need(smallest_area>1e-6,"zero_area_or_corrupt_GLTF_triangles")
        global_bounds=[]
        for axis in range(3):
            vals=[node.get("translation",[0,0,0])[axis] +
                  node.get("scale",[1,1,1])[axis]*point[axis]
                  for point in positions]
            global_bounds.append([min(vals),max(vals)])
        expected_bounds=[
            [expected_location[j]-expected_scale[j],
             expected_location[j]+expected_scale[j]] if source["kind"]=="circle" or j!=1
            else [expected_location[j],expected_location[j]]
            for j in range(3)]
        need(all(numbers_equal(observed_pair,expected_pair)
                 for observed_pair,expected_pair in zip(global_bounds,expected_bounds)),
             "actual_world_vertex_extents_differ_from_project")
        observed.append({
            "source_name":source["source_name"],"native_name":node["name"],
            "source_id_exactly_bound":"node_id" in source,
            "mesh":mesh["name"],"world_translation":expected_location,
            "world_scale":expected_scale,"actual_world_bounds":global_bounds,
            "PBR_baseColorRGBA":color,"vertices":len(positions),
            "triangles":len(ids)//3,"minimum_raw_triangle_area":smallest_area,
            "source_position_accessor_SHA256":sha(
                repr(positions).encode("utf8")),
        })
    need(set(actual_names)==set(range(len(nodes))),
         "extra_unsourced_glTF_nodes")
    return {
        "schema":SCHEMA,"GLB_sha256":original_GLB_SHA256,
        "classification":"SOURCE_PLAN_TO_DECODED_GLB_GEOMETRY_ONLY_NOT_EDITABLE_BLEND",
        "profile":"Blender4.5.14 PlaneCylinder v1",
        "object_count":len(observed),
        "object_reports":observed,
        "fully_decoded_every_position_normal_uv_and_index":True,
        "actual_binary_geometry_and_world_bounds_verified":True,
        "source_Project_UUIDs_explicitly_checked":all(
            "node_id" in node for node in source_nodes),
        "native_blend_source_reopen_executed":False,
        "Broker_or_original_Host_grants_verified_here":False,
        "GLB_editable_Blend_import_export_or_reopen_verified":False,
        "external_rights_or_human_aesthetics_approved":False,
    }
