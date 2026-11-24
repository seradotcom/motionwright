import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import type { NativeCapsule } from "./creativeProduction";
import { nativeRelinkCandidates, relinkRequest, proposeNativeSourceRelink, type ProductionRelinkInput } from "./nativeSourceRelink";
import {
  acknowledgeProductionDraft, createProductionDraft, decodeProductionCopy, editProductionDraft,
  encodeProductionCopy, inspectProductionOutcome, prepareProductionSubmission, productionConflict,
  productionPreparationMatches, productionUnconfirmed, reconcileProductionDraft, releaseProductionOutcome, resolveProductionDraft,
} from "./productionDesignDraft";
import type { ProductionDraft } from "./productionDesignDraft";
const request = "00000000-0000-4000-8000-000000000041", writer = "00000000-0000-4000-8000-000000000042";
async function fixture() {
  const project = structuredClone(fixtureProject); project.locks = []; project.branch_workspaces = [];
  const asset = project.assets.find(a => a.name === "native-demo.glb")!;
  const replacement = {...asset, id:crypto.randomUUID(), name:"Retained source.glb", content_sha256:"12".repeat(32)};
  const other = {...replacement,id:crypto.randomUUID(), name:"Incompatible.txt",media_type:"text/plain",content_sha256:"34".repeat(32)};
  project.assets.push(replacement, other);
  const capsule: NativeCapsule = { id:crypto.randomUUID(),scene_id:project.scenes[0].id,
    source_asset_id:asset.id,source_sha256:asset.content_sha256!,label:"Existing source",
    fidelity:{renderer:"blender",renderer_version:"old",visual:"native",temporal:"native",structural:"native",editable:"native",losses:[],evidence_sha256:"ab".repeat(32)},
    editable_parameters:["camera"],native_editor_hint:"Trusted editor",property_fidelity:[{path:"camera.focal_length",fidelity:"native",loss_note:null}] };
  const saved = await applyChange(project,{type:"upsert_native_capsule",capsule});
  const target = {kind:"relink" as const,sceneId:capsule.scene_id,capsuleId:capsule.id};
  const initial = createProductionDraft(saved,target);
  const draft = editProductionDraft(initial,{...initial.input as ProductionRelinkInput,assetId:replacement.id,assetSha256:replacement.content_sha256});
  return {project:saved,asset,replacement,other,capsule,target,draft};
}
function raw(draft:ProductionDraft) {
  return encodeProductionCopy({schema:"motionwright.production-recovery/1",scope:draft.scope,writerId:writer,updatedAt:1,draft});
}
describe("conditional native source relink",()=>{
  it("preserves original assets, scene and capsule identity while resetting claims",async()=>{
    const {project,capsule,replacement,draft}=await fixture();
    expect(nativeRelinkCandidates(project,capsule).map(a=>a.id)).toEqual([replacement.id]);
    const prepared=await prepareProductionSubmission(draft,project,request);
    const saved=await applyChange(project,prepared.change);
    const current=saved.production_design!.capsules.find(c=>c.id===capsule.id)!;
    expect(current.source_asset_id).toBe(replacement.id);
    expect(current.source_sha256).toBe(replacement.content_sha256);
    expect(current.label).toBe(capsule.label);
    expect(current.scene_id).toBe(capsule.scene_id);
    expect(current.fidelity).toMatchObject({renderer:"source-relink",visual:"unavailable",temporal:"unavailable",structural:"unavailable",editable:"unavailable",evidence_sha256:null});
    expect(current.editable_parameters).toEqual([]);
    expect(current.property_fidelity).toBeUndefined();
    expect(saved.assets).toEqual(project.assets);
    expect(saved.scenes).toEqual(project.scenes);
    expect(saved.revision).toBe(project.revision+1);
    const acknowledged=acknowledgeProductionDraft(prepared.draft,request,saved);
    expect(acknowledged.input).toMatchObject({sourceAssetId:replacement.id,sourceSha256:replacement.content_sha256,assetId:"",assetSha256:""});
    expect(acknowledged.intent).toBe(false);
    expect(acknowledged.pending).toBeNull();
  });
  it("rejects stale, incompatible, no-op and locked targets before mutation",async()=>{
    const {project,capsule,other,draft}=await fixture();
    const relink=relinkRequest(capsule.id,capsule.scene_id,draft.input as ProductionRelinkInput);
    for(const changed of [{...relink,expected_source_sha256:"f".repeat(64)},{...relink,source_sha256:"f".repeat(64)},
      {...relink,source_asset_id:other.id,source_sha256:other.content_sha256},
      {...relink,scene_id:project.scenes[1].id},{...relink,source_asset_id:capsule.source_asset_id,source_sha256:capsule.source_sha256}]) {
      expect(()=>proposeNativeSourceRelink(project,changed)).toThrow();
      await expect(applyChange(project,{type:"relink_native_capsule_source",relink:changed})).rejects.toThrow();
    }
    const locked=await applyChange(project,{type:"set_lock",resource:"scene:"+capsule.scene_id,kind:"content",note:"Source locked"});
    await expect(applyChange(locked,{type:"relink_native_capsule_source",relink})).rejects.toThrow();
    expect(productionConflict(draft,locked)).toBe(true);
  });
  it("recovers UNKNOWN without replay, refuses altered evidence and prevents silent rebasing",async()=>{
    const {project,draft}=await fixture(),prepared=await prepareProductionSubmission(draft,project,request);
    const recovered=decodeProductionCopy(raw(prepared.draft),draft.scope,writer).draft;
    const saved=await applyChange(project,prepared.change);
    expect(productionUnconfirmed(recovered)).toBe(true);
    expect(reconcileProductionDraft(recovered,saved)).toBe(recovered);
    await expect(prepareProductionSubmission(recovered,saved,writer)).rejects.toThrow("unconfirmed");
    const review=await inspectProductionOutcome(recovered,saved,{
      readProject:async()=>saved,readHistory:async()=>[{revision:saved.revision,created_at:"2026-11-24T00:00:00Z",change:prepared.change}]
    });
    expect(review.matchingValueRevisions).toEqual([saved.revision]);
    const continued=releaseProductionOutcome(recovered,saved,review);
    await expect(prepareProductionSubmission(continued,saved,writer)).rejects.toThrow("source changed");
    expect(()=>resolveProductionDraft(draft,saved,"draft")).toThrow("source changed");
    expect(productionPreparationMatches(draft,project,draft,{...project,assets:[]})).toBe(false);
    for(const mutate of [(copy:any)=>{copy.draft.pending.value.fidelity.evidence_sha256="ab".repeat(32);},
      (copy:any)=>{copy.draft.pending.input.assetSha256="f".repeat(64);},
      (copy:any)=>{copy.draft.input.grant="injected";}]) {
      const copy=JSON.parse(raw(prepared.draft));mutate(copy);
      expect(()=>decodeProductionCopy(JSON.stringify(copy),draft.scope,writer)).toThrow();
    }
  });
});
