/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { createNativeSourceDraft, prepareNativeSourceSpec, decodeNativeSourceCopy, encodeNativeSourceCopy } from "./nativeSourceDraft";
import { BLENDER_PRODUCT_RECIPE_MIME, blenderProductMetadata, blenderProductSceneIssues, blenderRecipeFile,
  buildBlenderProductRecipe, newBlenderProductInput, parseBlenderProductInput,withProductCameraPreset,withoutProductCameraMotion } from "./blenderProductRecipe";
import nativeFixture from "../../../crates/native/tests/fixtures/blender-product-studio-recipe.json?raw";
import type { Project } from "./types";

const capsuleId="12345678-1234-4567-89ab-123456789001";
const writerId="12345678-1234-4567-89ab-123456789002";
const authored={namespace:"StudioVessel",coating:"graphite",roughness:"0.37",metallic:"0.72",
  samples:"96",annotation:"AUTHOR SOURCE\nGRAPHITE",camera_curve:"linear"};
function project():Project{
  const p=structuredClone(fixtureProject);
  const scene=p.scenes[0];
  Object.assign(scene,{renderer:"blender",start:{num:"0",den:"1"},duration:{num:"40",den:"1"},nodes:[],beats:[],
    camera:{center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:0.05}});
  p.production_design=emptyProductionDesign();p.locks=[];
  return p;
}
describe("Blender ProductScene v1 authored source",()=>{
  it("produces the exact closed native fixture and real SHA-256 with no execution fields",()=>{
    const file=blenderRecipeFile(JSON.stringify(authored),capsuleId);
    expect(file.text).toBe(nativeFixture);
    expect(file.sha256).toBe("7fa825297049923d0054e65444e6d4d10e4a519b5f602ddc7f63474fa11ce9d2");
    expect(file.byteLength).toBe(new TextEncoder().encode(nativeFixture).length);
    expect(file.name).toBe("blender-product-StudioVessel.json");
    expect(Object.keys(file.recipe)).toEqual(["version","namespace","coating","roughness","metallic","samples","annotation","camera_curve"]);
    expect(blenderProductMetadata(JSON.stringify(authored),capsuleId)).toMatchObject({
      name:file.name,mediaType:BLENDER_PRODUCT_RECIPE_MIME,blenderRecipeExport:file.sha256,path:"",
    });
  });
  it.each([
    ["namespace","../escape"],["namespace",""],["namespace","a".repeat(33)],
    ["coating","arbitrary"],["camera_curve","native_default"],["roughness",""],
    ["roughness","0."+"9".repeat(70)],["roughness","1.1"],["roughness","1e"],
    ["roughness","-0.1"],["metallic","NaN"],["samples","15"],["samples","257"],
    ["samples","1.5"],["annotation",""],["annotation","A\nB\nC"],
    ["annotation","é"],["annotation","A\r\nB"],["annotation","a".repeat(33)],
  ])("rejects unsupported %s (%s)",(field,value)=>{
    expect(()=>buildBlenderProductRecipe({...authored,[field]:value})).toThrow();
  });
  it("allows exact numeric boundaries and explicitly selected native curve",()=>{
    const result=buildBlenderProductRecipe({...authored,roughness:"0",metallic:"1",samples:"16",camera_curve:"bezier_auto_clamped"});
    expect(result).toMatchObject({roughness:0,metallic:1,samples:16,camera_curve:"bezier_auto_clamped"});
    expect(buildBlenderProductRecipe(newBlenderProductInput(project().scenes[0].id))).toBeDefined();
  });
  it("rejects extra raw input fields, but retains incomplete authored values",()=>{
    expect(()=>parseBlenderProductInput(JSON.stringify({...authored,grant:"none"}))).toThrow();
    expect(parseBlenderProductInput(JSON.stringify({...authored,roughness:"1e"})).roughness).toBe("1e");
  });
  it("requires a frame-aligned, empty Blender scene and preserves incompatible composition",()=>{
    const p=project(), scene=p.scenes[0];
    expect(blenderProductSceneIssues(p,scene,capsuleId)).toEqual([]);
    const before=structuredClone(p);
    scene.duration={num:"39",den:"1"};scene.start={num:"1",den:"7"};
    scene.camera.zoom=2;
    expect(blenderProductSceneIssues(p,scene,capsuleId)).toHaveLength(2);
    expect(p.scenes[0].camera.zoom).toBe(2);
    p.scenes[0]=before.scenes[0];p.locks.push({id:writerId,resource:"scene:"+scene.id,kind:"renderer",note:"keep source"});
    expect(blenderProductSceneIssues(p,p.scenes[0],capsuleId)).toContain("The saved project or scene is content/renderer locked.");
  });
  it("retains the earlier six-field draft and only admits the explicitly prepared current recipe",()=>{
    const p=project(),scene=p.scenes[0],draft=createNativeSourceDraft(p,scene.id,"new",capsuleId);
    expect(decodeNativeSourceCopy(encodeNativeSourceCopy(draft,writerId),draft.scope,writerId)).toEqual(draft);
    draft.fields={...blenderProductMetadata(JSON.stringify(authored),capsuleId),path:"E:/sources/recipe.json"};
    const restored=decodeNativeSourceCopy(encodeNativeSourceCopy(draft,writerId),draft.scope,writerId);
    expect(restored).toEqual(draft);
    const spec=prepareNativeSourceSpec(restored.fields,capsuleId,scene.id);
    expect(spec.media_type).toBe(BLENDER_PRODUCT_RECIPE_MIME);
    expect(Object.keys(spec)).toHaveLength(7);
    expect(()=>prepareNativeSourceSpec({...draft.fields,blenderRecipe:JSON.stringify({...authored,roughness:"0.5"})},capsuleId,scene.id)).toThrow("prepared recipe file");
    expect(()=>prepareNativeSourceSpec({...draft.fields,mediaType:"application/json"},capsuleId,scene.id)).toThrow("prepared recipe file");
  });
});


describe("bounded native spatial camera authoring",()=>{
  it("preserves the legacy digest and retains independent or combined movements",()=>{
    const arc=withProductCameraPreset(authored,"arc_reveal");
    const dolly=withProductCameraPreset(authored,"dolly_focus");
    expect(buildBlenderProductRecipe(arc).camera_motion).toEqual({arc_degrees:25,dolly_percent:0});
    expect(buildBlenderProductRecipe(dolly).camera_motion).toEqual({arc_degrees:0,dolly_percent:12});
    const combined={...arc,dolly_percent:"18"};
    const file=blenderRecipeFile(JSON.stringify(combined),capsuleId);
    expect(file.recipe.camera_motion).toEqual({arc_degrees:25,dolly_percent:18});
    expect(blenderRecipeFile(JSON.stringify(withoutProductCameraMotion(arc)),capsuleId).text).toBe(nativeFixture);
    expect(file.sha256).not.toBe(blenderRecipeFile(JSON.stringify(authored),capsuleId).sha256);
    const p=project(),scene=p.scenes[0],draft=createNativeSourceDraft(p,scene.id,"new",capsuleId);
    draft.fields={...blenderProductMetadata(JSON.stringify(combined),capsuleId),path:"E:/sources/spatial.json"};
    const restored=decodeNativeSourceCopy(encodeNativeSourceCopy(draft,writerId),draft.scope,writerId);
    expect(restored).toEqual(draft);
    expect(prepareNativeSourceSpec(restored.fields,capsuleId,scene.id).media_type).toBe(BLENDER_PRODUCT_RECIPE_MIME);
    expect(()=>prepareNativeSourceSpec({...draft.fields,blenderRecipe:JSON.stringify({...combined,arc_degrees:"35"})},capsuleId,scene.id)).toThrow(/prepared recipe file/);
  });
  it("keeps incomplete camera input but rejects unsafe or implicit paths",()=>{
    const arc=withProductCameraPreset(authored,"arc_reveal");
    for(const [a,d] of [["0","0"],["9","0"],["36","0"],["0","4"],["0","19"],["1e","12"],["20","1.5"]]){
      const raw={...arc,arc_degrees:a,dolly_percent:d};
      expect(parseBlenderProductInput(JSON.stringify(raw))).toEqual(raw);
      expect(()=>buildBlenderProductRecipe(raw)).toThrow();
    }
    expect(()=>buildBlenderProductRecipe({...arc,camera_curve:"bezier_auto_clamped"})).toThrow();
    const incomplete={...arc};delete incomplete.dolly_percent;
    expect(()=>parseBlenderProductInput(JSON.stringify(incomplete))).toThrow();
  });
});
