import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { inspectVariantContinuity } from "./variantContinuity";
import type { Project, DeliverableProfile } from "./types";
import type { ProductionPlan } from "./creativeProduction";

const SHA="a".repeat(64);
function source(): Project {
  const project=structuredClone(fixtureProject);
  project.locks=[];
  project.audio.transcript=[];
  project.narrative.protected_sections=[];
  project.brief.claims=[];
  const plan:ProductionPlan={
    objective:"An original product film",audience:"Technical reviewers",
    concept:"Show native semantic action and its evidence.",
    reference_constraints:[],exclusions:[],
    shots:project.scenes.map(scene=>({
      scene_id:scene.id,purpose:scene.objective,claim_ids:[],asset_ids:[],
      evidence_kind:"graphic_study",
    })),clock:{kind:"timeline"},approval:null,
  };
  project.production_design={
    plan,heroes:[],capsules:[],patches:[],brand_profile:null,
    taste_profile:null,brand_exceptions:[],project_decisions:[],
  };
  return project;
}
function variant(project:Project):DeliverableProfile {
  return structuredClone(project.deliverables[0]);
}
function addedClaim(project:Project,sceneIndex:number,kind:"manual"|"source"="manual") {
  const claimId=crypto.randomUUID();
  const sourceId=crypto.randomUUID();
  project.assets.push({
    id:sourceId,name:"Actual authorized capture.png",media_type:"image/png",
    content_sha256:SHA,source_revision:"synthetic-test-only",
  });
  project.brief.claims.push({
    id:claimId,text:"A user-authored product behavior claim",
    source:kind==="source"?{kind:"asset",asset_id:sourceId}:{kind:"manual_note",label:"Unverified testimony"},
    context:"not yet independently reviewed",source_revision:"test-input",
  });
  const shot=project.production_design!.plan!.shots[sceneIndex];
  shot.claim_ids.push(claimId);
  shot.asset_ids.push(sourceId);
  shot.evidence_kind=kind==="source"?"real_capture":"graphic_study";
  return {claimId,sourceId};
}

describe("MW05-E14-02 source-bound narrative variant continuity",()=>{
  it("reports a pristine exact 36-second project cut without pretending to approve its media",()=>{
    const p=source(),profile=variant(p);
    const original=structuredClone(p);
    const result=inspectVariantContinuity(p,profile);
    expect(result.verdict).toBe("NO_AUTOMATED_FLAGS");
    expect(result.selected_scene_ids).toEqual(p.scenes.map(s=>s.id));
    expect(result.total_duration).toEqual({num:"36",den:"1"});
    expect(result.omitted_scene_ids).toEqual([]);
    expect(result.findings).toEqual([]);
    expect(p).toEqual(original);
  });
  it("abstains from claim coverage when no production plan is stored",()=>{
    const p=source();p.production_design!.plan=null;
    const result=inspectVariantContinuity(p,variant(p));
    expect(result.verdict).toBe("EDITOR_REVIEW_REQUIRED");
    expect(result.findings.map(x=>x.code)).toContain("PLAN_NOT_ADMITTED");
    expect(result.findings.map(x=>x.code)).toContain("SHOT_INTENT_UNKNOWN");
  });
  it("finds a source-scene removal that loses the only authored claim",()=>{
    const p=source();const {claimId}=addedClaim(p,3);
    const profile=variant(p);profile.included_scene_ids=p.scenes.slice(0,2).map(s=>s.id);
    const report=inspectVariantContinuity(p,profile);
    expect(report.selected_scene_ids).toHaveLength(2);
    expect(report.total_duration).toEqual({num:"16",den:"1"});
    expect(report.omitted_scene_ids).toEqual(p.scenes.slice(2).map(s=>s.id));
    expect(report.omitted_claim_ids).toEqual([claimId]);
    expect(report.findings.find(x=>x.code==="CLAIM_LOST")?.claim_id).toBe(claimId);
  });
  it("does not lose a claim when another retained scene still supports it",()=>{
    const p=source();const {claimId}=addedClaim(p,3,"source");
    p.production_design!.plan!.shots[0].claim_ids.push(claimId);
    p.production_design!.plan!.shots[0].asset_ids.push(p.brief.claims[0].source!.kind==="asset"?
      (p.brief.claims[0].source as {kind:"asset";asset_id:string}).asset_id:"");
    p.production_design!.plan!.shots[0].evidence_kind="real_capture";
    const profile=variant(p);profile.included_scene_ids=p.scenes.slice(0,2).map(s=>s.id);
    expect(inspectVariantContinuity(p,profile).omitted_claim_ids).toEqual([]);
  });
  it("rejects unrelated media even when both assets have valid hashes",()=>{
    const p=source();addedClaim(p,0,"source");
    p.production_design!.plan!.shots[0].asset_ids=[p.assets[1].id];
    const report=inspectVariantContinuity(p,variant(p));
    expect(report.findings.map(f=>f.code)).toContain("CLAIM_NOT_SOURCE_BOUND");
  });
  it("labels authored graphic studies as insufficient real-product evidence",()=>{
    const p=source();addedClaim(p,0,"manual");
    const result=inspectVariantContinuity(p,variant(p));
    expect(result.findings.map(x=>x.code)).toContain("CLAIM_NOT_SOURCE_BOUND");
    expect(result.verdict).toBe("EDITOR_REVIEW_REQUIRED");
  });
  it("preserves source identity but never independently verifies the factual claim",()=>{
    const p=source();const {claimId}=addedClaim(p,0,"source");
    const result=inspectVariantContinuity(p,variant(p));
    expect(result.retained_claim_ids).toContain(claimId);
    expect(result.findings.map(x=>x.code)).not.toContain("CLAIM_NOT_SOURCE_BOUND");
    expect(result.verdict).toBe("NO_AUTOMATED_FLAGS");
  });
  it("does not treat an audio-only source as observed image capture",()=>{
    const p=source();const {claimId,sourceId}=addedClaim(p,0,"source");
    p.assets.find(a=>a.id===sourceId)!.media_type="audio/wav";
    const result=inspectVariantContinuity(p,variant(p));
    expect(result.retained_claim_ids).toContain(claimId);
    expect(result.findings.map(x=>x.code)).toContain("CLAIM_NOT_SOURCE_BOUND");
  });
  it("rejects removing a protected scene from an authored social cut",()=>{
    const p=source(),profile=variant(p);
    profile.included_scene_ids=[p.scenes[0].id,p.scenes[3].id];
    profile.protected_scene_ids=[p.scenes[2].id];
    const result=inspectVariantContinuity(p,profile);
    expect(result.verdict).toBe("STRUCTURAL_BLOCKERS");
    expect(result.findings.map(x=>x.code)).toContain("OMITTED_PROTECTED");
  });
  it("rejects duplicated, reordered, orphaned and empty custom selections",()=>{
    const p=source(),profile=variant(p);
    profile.included_scene_ids=[p.scenes[1].id,p.scenes[0].id,p.scenes[0].id];
    let result=inspectVariantContinuity(p,profile);
    expect(result.findings.map(x=>x.code)).toContain("DUPLICATE_SCENE");
    expect(result.findings.map(x=>x.code)).toContain("SCENE_ORDER");
    profile.included_scene_ids=[crypto.randomUUID()];
    result=inspectVariantContinuity(p,profile);
    expect(result.findings.map(x=>x.code)).toEqual(expect.arrayContaining(["UNKNOWN_SCENE","EMPTY_CUT"]));
  });
  it("compares derivative claim coverage to the parent cut, not always the full original",()=>{
    const p=source();
    p.deliverables[0].included_scene_ids=[p.scenes[0].id,p.scenes[2].id];
    const {claimId}=addedClaim(p,3);
    const child=variant(p);child.id=crypto.randomUUID();
    child.parent_profile_id=p.deliverables[0].id;
    child.source_revision=p.revision;
    child.included_scene_ids=[p.scenes[0].id];
    expect(inspectVariantContinuity(p,child).omitted_claim_ids).not.toContain(claimId);
    expect(inspectVariantContinuity(p,child).omitted_scene_ids).toEqual([p.scenes[2].id]);
  });
  it("detects a missing derivative parent and unsafe self-parenting",()=>{
    const p=source(),profile=variant(p);
    profile.parent_profile_id=crypto.randomUUID();
    expect(inspectVariantContinuity(p,profile).findings.map(f=>f.code)).toContain("UNKNOWN_PARENT");
    profile.parent_profile_id=profile.id;
    expect(inspectVariantContinuity(p,profile).findings.map(f=>f.code)).toContain("CYCLIC_PARENT");
  });
  it("identifies unlocalized text and reused voice sources without faking a translation",()=>{
    const p=source(),child=structuredClone(p.deliverables[1]);
    const parent=p.deliverables[0];parent.voice_track_id=crypto.randomUUID();
    child.parent_profile_id=parent.id;
    child.language="es-MX";child.voice_track_id=parent.voice_track_id;
    child.text_overrides={};
    const report=inspectVariantContinuity(p,child);
    expect(report.verdict).toBe("STRUCTURAL_BLOCKERS");
    expect(report.findings.map(f=>f.code)).toEqual(expect.arrayContaining(["LOCALIZED_COPY_INCOMPLETE","PARENT_VOICE_REUSED"]));
  });
  it("reports a transcript that overlaps an included scene boundary without clipping it",()=>{
    const p=source(),profile=variant(p);
    profile.included_scene_ids=[p.scenes[0].id,p.scenes[2].id];
    p.audio.transcript=[{
      id:crypto.randomUUID(),voice_track_id:crypto.randomUUID(),
      start:{num:"6",den:"1"},end:{num:"8",den:"1"},
      text:"This sentence crosses two scenes",speaker:null,alignment:{kind:"manual"},
    }];
    const before=structuredClone(p.audio.transcript);
    const report=inspectVariantContinuity(p,profile);
    expect(report.findings.map(f=>f.code)).toContain("CAPTION_CROSSES_CUT");
    expect(p.audio.transcript).toEqual(before);
  });
  it("preserves rational duration exactly without floating-point scene drift",()=>{
    const p=source();p.scenes[0].duration={num:"1001",den:"30000"};
    p.scenes[1].duration={num:"1001",den:"30000"};
    const profile=variant(p);profile.included_scene_ids=p.scenes.slice(0,2).map(s=>s.id);
    expect(inspectVariantContinuity(p,profile).total_duration).toEqual({num:"1001",den:"15000"});
  });
  it("does not silently omit protected narrative beats without a scene mapping",()=>{
    const p=source();p.narrative.protected_sections=[{beat_id:crypto.randomUUID(),reason:"Keep the original narrative claim"}];
    const r=inspectVariantContinuity(p,variant(p));
    expect(r.findings.map(f=>f.code)).toContain("UNMAPPED_PROTECTED_BEATS");
  });
  it("abstains on oversized source sets rather than emitting a false clean report",()=>{
    const p=source();p.scenes=Array(4097).fill(p.scenes[0]);
    expect(()=>inspectVariantContinuity(p,variant(p))).toThrow("budget");
  });
});
