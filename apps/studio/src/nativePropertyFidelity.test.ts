import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import type { NativeCapsule, NativePropertyFidelity } from "./creativeProduction";
import { validateNativePropertyFidelity } from "./creativeProduction";

function source() {
  const project = structuredClone(fixtureProject);
  project.schema_version=2;
  const asset=project.assets.find(x=>x.name==="native-demo.glb")!;
  const capsule:NativeCapsule={
    id:crypto.randomUUID(),
    scene_id:project.scenes[1].id,
    source_asset_id:asset.id,source_sha256:asset.content_sha256!,
    label:"Original GLB source",
    fidelity:{
      renderer:"blender",renderer_version:"unverified",
      visual:"unavailable",temporal:"unavailable",structural:"native",
      editable:"unavailable",
      losses:["Source retained; rendering and parameter edits not tested"],
      evidence_sha256:null,
    },
    editable_parameters:[],
    native_editor_hint:"Open in a trusted source editor",
    property_fidelity:[],
  };
  return {project,capsule};
}
const annotate=(path:string,fidelity:NativePropertyFidelity["fidelity"],loss_note:string|null=null):
 NativePropertyFidelity=>({path,fidelity,loss_note});

describe("MW05-E04-02 explicit property fidelity negotiation",()=>{
  it("accepts independently named source properties with loss descriptions",()=>{
    const properties=[
      annotate("camera.focal_length","baked","Optical focal length cannot be edited in the output timeline"),
      annotate("geometry.mesh","native"),
      annotate("materials.base_color","translated","Source shader may differ from target workspace"),
    ];
    expect(()=>validateNativePropertyFidelity(properties)).not.toThrow();
  });
  it("rejects ambiguous or executable-looking semantic paths",()=>{
    for(const path of ["",".camera","camera.","geometry..mesh","0camera.zoom",
      "camera-value","a.b.c.d.e.f","__proto__","camera.__proto__",
      "a.constructor","a.prototype","native/source/scene","x".repeat(129)]) {
      expect(()=>validateNativePropertyFidelity([annotate(path,"native")]),path).toThrow();
    }
  });
  it("rejects duplicate classification names",()=>{
    expect(()=>validateNativePropertyFidelity([
      annotate("camera.zoom","native"),annotate("camera.zoom","unavailable","No supported importer")
    ])).toThrow("Duplicate");
  });
  it("requires meaningful loss descriptions for baked, approximate, unavailable",()=>{
    for(const status of ["baked","approximate","unavailable"] as const) {
      expect(()=>validateNativePropertyFidelity([annotate("animation.keyframes",status)])).toThrow("need");
      expect(()=>validateNativePropertyFidelity([annotate("animation.keyframes",status,"   ")])).toThrow();
      expect(()=>validateNativePropertyFidelity([
        annotate("animation.keyframes",status,"Imported interpolation would be flattened")
      ])).not.toThrow();
    }
  });
  it("rejects source metadata expansion past 64 bounded properties",()=>{
    expect(()=>validateNativePropertyFidelity(Array.from({length:65},(_,i)=>
      annotate("camera.p"+i,"native")))).toThrow("64");
  });
  it("rejects invalid or oversized loss notes",()=>{
    expect(()=>validateNativePropertyFidelity([
      annotate("geometry.mesh","approximate","y".repeat(501))
    ])).toThrow();
    expect(()=>validateNativePropertyFidelity([
      annotate("geometry.mesh","approximate","loss\0unknown")
    ])).toThrow();
  });
  it("commits per-property report through the same source-bound transaction",async()=>{
    const {project,capsule}=source();
    const before=structuredClone(project);
    capsule.property_fidelity=[
      annotate("camera.focal_length","baked","An authored focal length cannot be edited in this environment"),
      annotate("animation.keyframes","unavailable","Original keyframes remain in GLB; no admitted native animation editor"),
    ];
    const next=await applyChange(project,{type:"upsert_native_capsule",capsule});
    expect(next.revision).toBe(project.revision+1);
    expect(next.production_design?.capsules[0].property_fidelity).toEqual(capsule.property_fidelity);
    expect(next.production_design?.capsules[0].editable_parameters).toEqual([]);
    expect(next.production_design?.capsules[0].fidelity.evidence_sha256).toBeNull();
    expect(next.assets).toEqual(project.assets);
    expect(project).toEqual(before);
  });
  it("a subsequent classification update preserves same capsule identity and original asset",async()=>{
    const {project,capsule}=source();
    const first=await applyChange(project,{type:"upsert_native_capsule",capsule});
    const edited:NativeCapsule={...structuredClone(capsule),property_fidelity:[
      annotate("geometry.mesh","translated","Retain GLB original for manual verification"),
    ]};
    const after=await applyChange(first,{type:"upsert_native_capsule",capsule:edited});
    expect(after.production_design?.capsules).toHaveLength(1);
    expect(after.production_design?.capsules[0].id).toBe(capsule.id);
    expect(after.production_design?.capsules[0].property_fidelity).toEqual(edited.property_fidelity);
    expect(after.assets).toEqual(first.assets);
    expect(after.scenes).toEqual(first.scenes);
  });
  it("a source digest mismatch must be rejected and leave project unchanged",async()=>{
    const {project,capsule}=source();
    capsule.source_sha256="b".repeat(64);
    capsule.property_fidelity=[annotate("camera.focal_length","native")];
    await expect(applyChange(project,{type:"upsert_native_capsule",capsule})).rejects.toThrow("digest-bound");
    expect(project.production_design?.capsules ?? []).toHaveLength(0);
  });
  it("legacy capsules with no property metadata remain representable",async()=>{
    const {project,capsule}=source();
    delete capsule.property_fidelity;
    const next=await applyChange(project,{type:"upsert_native_capsule",capsule});
    expect(next.production_design?.capsules[0].property_fidelity ?? []).toEqual([]);
  });
  it("never auto-enables rendering, imported execution, parameter grants or scene edits",async()=>{
    const {project,capsule}=source();
    capsule.property_fidelity=[annotate("camera.zoom","native")];
    const next=await applyChange(project,{type:"upsert_native_capsule",capsule});
    const stored=next.production_design!.capsules[0];
    expect(stored.fidelity.editable).toBe("unavailable");
    expect(stored.fidelity.visual).toBe("unavailable");
    expect(stored.editable_parameters).toHaveLength(0);
    expect(stored.property_fidelity?.[0].fidelity).toBe("native");
    expect(next.scenes).toEqual(project.scenes);
  });
});
