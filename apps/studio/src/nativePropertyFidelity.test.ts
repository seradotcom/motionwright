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

import { clearNativePropertyEditor, editNativePropertyReview, nativeFidelityInput, proposeNativeFidelity,
  removeNativePropertyReview, stageNativePropertyReview, type ProductionFidelityInput } from "./nativePropertyFidelityDraft";
import { acknowledgeProductionDraft, createProductionDraft, decodeProductionCopy, editProductionDraft, encodeProductionCopy,
  inspectProductionOutcome, prepareProductionSubmission, productionConflict, productionPreparationMatches, productionScope,
  productionUnconfirmed, readProductionRecoveryPointer, reconcileProductionDraft, releaseProductionOutcome, resolveProductionDraft,
  type ProductionDraft } from "./productionDesignDraft";
import type { Change, Project } from "./types";

const writer = "00000000-0000-4000-8000-000000000044", request = "00000000-0000-4000-8000-000000000045";
async function reviewSource() {
  const { project, capsule } = source(); project.locks = [];
  capsule.editable_parameters = ["camera.center_x"];
  const saved = await applyChange(project, { type: "upsert_native_capsule", capsule });
  const target = { kind: "fidelity" as const, sceneId: capsule.scene_id, capsuleId: capsule.id };
  return { project: saved, capsule, target, draft: createProductionDraft(saved, target) };
}
function staged(draft: ProductionDraft, path = "camera.focal_length"): ProductionDraft {
  const input = draft.input as ProductionFidelityInput;
  return editProductionDraft(draft, stageNativePropertyReview({ ...input, path, fidelity: "baked", note: "The source value remains editable only in its original environment." }));
}
function recovered(draft: ProductionDraft): ProductionDraft {
  const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: draft.scope, writerId: writer, updatedAt: 1, draft });
  return decodeProductionCopy(raw, draft.scope, writer).draft;
}

describe("native source review authoring and retained original authority", () => {
  it("stages a complete review, edits a saved property and removes another in one existing capsule change", async () => {
    const state = await reviewSource(), original = structuredClone(state.project);
    let draft = staged(state.draft);
    draft = editProductionDraft(draft, stageNativePropertyReview({ ...(draft.input as ProductionFidelityInput),
      path: "geometry.mesh", fidelity: "native", note: "" }));
    expect(state.project).toEqual(original);
    const first = await prepareProductionSubmission(draft, state.project, request);
    expect(first.change.type).toBe("upsert_native_capsule");
    const saved = await applyChange(state.project, first.change), acknowledged = acknowledgeProductionDraft(first.draft, request, saved);
    expect(acknowledged.intent).toBe(false);
    let input = editNativePropertyReview(acknowledged.input as ProductionFidelityInput, "camera.focal_length");
    input = stageNativePropertyReview({ ...input, path: "camera.focal_length", fidelity: "translated", note: "The source value has an explicitly authored translation." });
    input = removeNativePropertyReview(input, "geometry.mesh");
    const next = await prepareProductionSubmission(editProductionDraft(acknowledged, input), saved, writer);
    const final = await applyChange(saved, next.change), capsule = final.production_design!.capsules.find(value => value.id === state.capsule.id)!;
    expect(capsule.property_fidelity).toEqual([annotate("camera.focal_length", "translated", input.entries[0].loss_note)]);
    expect({ ...capsule, property_fidelity: [] }).toEqual(state.capsule);
    expect(final.assets).toEqual(original.assets); expect(final.scenes).toEqual(original.scenes); expect(final.revision).toBe(original.revision + 2);
    const removed = removeNativePropertyReview(nativeFidelityInput(capsule), "camera.focal_length");
    const clear = await prepareProductionSubmission(editProductionDraft(createProductionDraft(final, state.target), removed), final, request);
    expect(clear.change.type === "upsert_native_capsule" && clear.change.capsule.property_fidelity).toBeUndefined();
  });
  it("retains incomplete property input and scoped review rows without treating them as a submitted review", async () => {
    const { project, target, draft } = await reviewSource(), edited = staged(draft);
    const raw = editProductionDraft(edited, { ...(edited.input as ProductionFidelityInput), path: "camera.", note: "  incomplete loss\n", fidelity: "approximate" });
    expect(recovered(raw).input).toEqual(raw.input);
    await expect(prepareProductionSubmission(raw, project, request)).rejects.toThrow("Add the current property");
    expect(productionScope(project, { ...target, capsuleId: request })).not.toBe(raw.scope);
    expect(readProductionRecoveryPointer({ getItem: () => null }, raw.scope, { observed: false, expectedPointer: null })).toEqual({ observed: true, expectedPointer: null });
    const foreign = { ...project, generation: request };
    expect(() => resolveProductionDraft(raw, foreign, "draft")).toThrow("another project");
    expect(() => editProductionDraft(raw, { ...(raw.input as ProductionFidelityInput), sourceSha256: "f".repeat(64) })).toThrow("retarget");
  });
  it("keeps an UNKNOWN original and later raw input across reload until the original exact response arrives", async () => {
    const { project, draft } = await reviewSource(), prepared = await prepareProductionSubmission(staged(draft), project, request);
    const restored = recovered(prepared.draft), observed = await applyChange(project, prepared.change);
    expect(productionUnconfirmed(restored)).toBe(true);
    expect(reconcileProductionDraft(restored, observed)).toBe(restored);
    await expect(prepareProductionSubmission(restored, observed, writer)).rejects.toThrow("unconfirmed");
    const later = editProductionDraft(restored, { ...(restored.input as ProductionFidelityInput), path: "animation.", note: "  pending next review " });
    const acknowledged = acknowledgeProductionDraft(later, request, observed);
    expect(acknowledged.pending).toBeNull(); expect(acknowledged.intent).toBe(true); expect(acknowledged.input).toEqual(later.input);
    expect(recovered(acknowledged).input).toEqual(later.input);
    expect(acknowledgeProductionDraft(restored, writer, observed)).toBe(restored);
    const bad = structuredClone(observed); bad.assets.find(asset => asset.id === (draft.input as ProductionFidelityInput).sourceAssetId)!.content_sha256 = "f".repeat(64);
    expect(() => acknowledgeProductionDraft(restored, request, bad)).toThrow("SHA-256");
  });
  it("never silently carries classifications onto replaced source bytes, even after an UNKNOWN outcome review", async () => {
    const { project, draft, capsule } = await reviewSource(), authored = staged(draft);
    const changed = structuredClone(project); changed.revision++;
    changed.assets.find(asset => asset.id === capsule.source_asset_id)!.content_sha256 = "f".repeat(64);
    const replacement = changed.production_design!.capsules.find(value => value.id === capsule.id)!; replacement.source_sha256 = "f".repeat(64);
    expect(productionConflict(authored, changed)).toBe(true);
    expect(() => resolveProductionDraft(authored, changed, "draft")).toThrow("source identity changed");
    const saved = resolveProductionDraft(authored, changed, "saved");
    expect((saved.input as ProductionFidelityInput).sourceSha256).toBe("f".repeat(64)); expect(saved.intent).toBe(false);
    const pending = (await prepareProductionSubmission(authored, project, request)).draft;
    const review = await inspectProductionOutcome(pending, changed, {
      readProject: async () => changed,
      readHistory: async () => [{ revision: changed.revision, created_at: "2026-11-24T00:00:00Z", change: { type: "upsert_native_capsule", capsule: replacement } as Change }],
    });
    expect(review.matchingValueRevisions).toEqual([]);
    const continued = releaseProductionOutcome(pending, changed, review);
    expect(recovered(continued).input).toEqual(authored.input);
    await expect(prepareProductionSubmission(continued, changed, writer)).rejects.toThrow("source identity changed");
    expect(productionPreparationMatches(authored, project, authored, { ...project, assets: changed.assets })).toBe(false);
  });
  it("rejects altered capsule metadata and execution fields in retained comparison copies", async () => {
    const { project, draft } = await reviewSource(), prepared = await prepareProductionSubmission(staged(draft), project, request);
    const raw = encodeProductionCopy({ schema: "motionwright.production-recovery/1", scope: prepared.draft.scope, writerId: writer, updatedAt: 1, draft: prepared.draft });
    for (const mutate of [
      (copy: any) => { copy.draft.input.effect_grant = "not-admitted"; },
      (copy: any) => { copy.draft.pending.value.editable_parameters.push("unapproved.parameter"); },
      (copy: any) => { copy.draft.pending.value.source_sha256 = "f".repeat(64); },
      (copy: any) => { copy.draft.target.capsuleId = request; },
      (copy: any) => { copy.draft.pending.input.path = "camera."; },
    ]) {
      const copy = JSON.parse(raw); mutate(copy); expect(() => decodeProductionCopy(JSON.stringify(copy), prepared.draft.scope, writer)).toThrow();
    }
    const absent = structuredClone(project); absent.production_design!.capsules = [];
    expect(() => proposeNativeFidelity(absent, null, prepared.draft.input as ProductionFidelityInput)).toThrow("unavailable");
  });
  it("requires an explicit row edit, rejects collisions and preserves an unfinished edit when another row is selected", async () => {
    const { draft } = await reviewSource();
    let input = staged(draft).input as ProductionFidelityInput;
    expect(() => stageNativePropertyReview({ ...input, path: "camera.focal_length", fidelity: "native" })).toThrow("already");
    input = stageNativePropertyReview({ ...input, path: "geometry.mesh", fidelity: "native", note: "" });
    let editing = editNativePropertyReview(input, "camera.focal_length");
    expect(() => stageNativePropertyReview({ ...editing, path: "geometry.mesh" })).toThrow("already");
    expect(() => editNativePropertyReview(editing, "geometry.mesh")).toThrow("current property");
    expect(() => removeNativePropertyReview(editing, "camera.focal_length")).toThrow("current edit");
    editing = stageNativePropertyReview({ ...editing, path: "camera.lens" });
    expect(editing.entries.map(entry => entry.path)).toEqual(["camera.lens", "geometry.mesh"]);
    expect(clearNativePropertyEditor({ ...editing, path: "unfinished." }).entries).toEqual(editing.entries);
  });
});
