import { describe, expect, it } from "vitest";
import { addFrameOffset, nativeFindingBody, planNativeInspection } from "./nativeInspection";
import { createNativeReviewRepairDraft, nativeRepairDraftIsCurrent } from "./nativeReviewRepair";
import type { ManualCreativeFinding } from "./nativeInspection";
import { fixtureProject } from "./fixture";
import type { MotionCanvasRenderEvidence } from "./types";

function fixture() {
  const project=structuredClone(fixtureProject),scene=project.scenes[0],profile=project.deliverables[0];
  const evidence:MotionCanvasRenderEvidence={project_resource:"project:"+project.id,generation:project.generation,revision:project.revision,deliverable_id:profile.id,
    frame_rate:{num:30,den:1},segments:[{segment_id:"native-inspection",scene_ids:project.scenes.slice(0,2).map(s=>s.id),frame_count:450,plan_ref:"plan",fingerprint:"fingerprint",job_ref:"job",artifact:{},verification:{}}],
    preview:[{token:"owned-native-frame-grant",segment_id:"native-inspection",scene_ids:project.scenes.slice(0,2).map(s=>s.id),frame_count:450}]};
  return {project,scene,profile,evidence};
}

describe("native inspection is a bounded source-granted read plan",()=>{
  it("samples at most eight frames and never leaks into the next scene",()=>{
    const {project,scene,profile,evidence}=fixture();
    const plan=planNativeInspection(project,scene,profile,evidence)!;
    expect(plan.samples.length).toBeLessThanOrEqual(8);
    expect(plan.samples.length).toBeGreaterThanOrEqual(3);
    expect(plan.samples[0].frameIndex).toBe(0);
    expect(plan.samples.at(-1)?.frameIndex).toBe(209);
    expect(plan.samples.map(s=>s.frameIndex)).toContain(104);
    expect(new Set(plan.samples.map(s=>s.frameIndex)).size).toBe(plan.samples.length);
    expect(plan.samples.every(s=>s.token==="owned-native-frame-grant")).toBe(true);
  });
  it("refuses stale revisions, wrong output profiles, missing grants and unbounded decodes",()=>{
    const {project,scene,profile,evidence}=fixture();
    expect(planNativeInspection(project,scene,profile,null)).toBeNull();
    expect(planNativeInspection({...project,revision:project.revision+1},scene,profile,evidence)).toBeNull();
    expect(planNativeInspection(project,scene,project.deliverables[1],evidence)).toBeNull();
    expect(planNativeInspection(project,scene,profile,{...evidence,preview:[]})).toBeNull();
    expect(planNativeInspection(project,scene,{...profile,width:16384,height:16384},evidence)).toBeNull();
  });
  it("preserves NTSC rational frame time instead of rounding it to UI milliseconds",()=>{
    expect(addFrameOffset({num:"7",den:"1"},1,{num:30000,den:1001})).toEqual({num:"211001",den:"30000"});
    expect(addFrameOffset({num:"0",den:"1"},30,{num:30000,den:1001})).toEqual({num:"1001",den:"1000"});
    expect(()=>addFrameOffset({num:"0",den:"1"},1,{num:0,den:1})).toThrow("bounded");
    expect(()=>addFrameOffset({num:"0",den:"1"},1.5,{num:30,den:1})).toThrow("bounded");
  });
  it("records a human finding with exact source evidence but never serializes its runtime grant",()=>{
    const {project,scene,profile,evidence}=fixture();
    const plan=planNativeInspection(project,scene,profile,evidence)!;
    const sample={frameIndex:plan.samples[0].frameIndex,timelineTime:plan.samples[0].timelineTime,pngSha256:"ab".repeat(32),width:plan.width,height:plan.height};
    const finding:ManualCreativeFinding={severity:"important",confidence:"probable",violatedConstraint:"Legible type during the entrance",observation:"The heading crosses the wordmark during this frame.",proposedRepair:"Move the heading's entrance origin without changing the final layout.",nodeId:null};
    const body=nativeFindingBody(plan,sample,finding),data=JSON.parse(body);
    expect(data.source.revision).toBe(project.revision);
    expect(data.source.png_sha256).toBe(sample.pngSha256);
    expect(data.interpretation).toBe("manual_review_not_automated_critic_verdict");
    expect(body).not.toContain("owned-native-frame-grant");
    expect(body).not.toContain('"PASS"');
    expect(()=>nativeFindingBody(plan,{...sample,frameIndex:999},finding)).toThrow("outside");
    expect(()=>nativeFindingBody(plan,{...sample,timelineTime:{num:"999",den:"1"}},finding)).toThrow("outside");
    expect(()=>nativeFindingBody(plan,sample,{...finding,observation:""})).toThrow("empty");
  });
});


describe("native review -> scoped repair handoff is never an automatic project edit",()=>{
  const checked=()=>{
    const {project,scene,profile,evidence}=fixture();
    const plan=planNativeInspection(project,scene,profile,evidence)!;
    const point=plan.samples[0];
    const sample={frameIndex:point.frameIndex,timelineTime:point.timelineTime,
      pngSha256:"ab".repeat(32),width:plan.width,height:plan.height};
    const target=scene.nodes.find(node=>node.kind==="text") ?? scene.nodes[0];
    const finding:ManualCreativeFinding={
      severity:"important",confidence:"probable",violatedConstraint:"Keep text clear at the native entrance",
      observation:"The selected frame has insufficient type separation.",
      proposedRepair:"Review this object's placement and manually choose a corrected value.",
      nodeId:target.id,
    };
    return {project,scene,plan,sample,finding,target};
  };
  it("transfers exact source context and selected object without editing a revision",()=>{
    const {project,scene,plan,sample,finding,target}=checked();
    const before=structuredClone(project);
    const draft=createNativeReviewRepairDraft(project,scene,plan,sample,finding);
    expect(draft).toMatchObject({schema:"motionwright.native-review-repair-draft/1",
      projectId:project.id,generation:project.generation,revision:project.revision,
      sceneId:scene.id,profileId:plan.profileId,frameIndex:sample.frameIndex,
      frameSha256:sample.pngSha256,targetNodeId:target.id,
      editKind:target.kind==="text"?"text":"transform"});
    expect(draft.rationale).toContain("repair NOT verified");
    expect(draft.rationale).toContain(sample.pngSha256);
    expect(draft.rationale).toContain("generation " + plan.generation);
    expect(draft.rationale).toContain("profile " + plan.profileId);
    expect(draft.rationale).toContain("Target object " + target.id);
    expect(draft.rationale).toContain("severity " + finding.severity);
    expect(draft.rationale).toContain("revision " + plan.revision);
    expect(draft.rationale).toContain(finding.proposedRepair);
    expect(draft.rationale).not.toContain("owned-native-frame-grant");
    expect(nativeRepairDraftIsCurrent(project,scene,draft,plan.profileId)).toBe(true);
    expect(project).toEqual(before);
  });
  it("refuses stale project generations, wrong sources and unselected objects",()=>{
    const {project,scene,plan,sample,finding}=checked();
    const create=(p=project,s=scene,selection=sample,note=finding)=>
      createNativeReviewRepairDraft(p,s,plan,selection,note);
    expect(()=>create(project,scene,sample,{...finding,nodeId:null})).toThrow("Select");
    expect(()=>create({...project,revision:project.revision+1})).toThrow("stale");
    expect(()=>create({...project,generation:crypto.randomUUID()})).toThrow("stale");
    expect(()=>createNativeReviewRepairDraft(project,scene,{...plan,profileId:crypto.randomUUID()},sample,finding)).toThrow("stale");
    expect(()=>createNativeReviewRepairDraft(project,scene,{...plan,width:plan.width+1},sample,finding)).toThrow("stale");
    expect(()=>create(project,scene,{...sample,pngSha256:"not-a-sha"})).toThrow("outside");
    expect(()=>create(project,scene,{...sample,frameIndex:999})).toThrow("outside");
    expect(()=>create(project,scene,sample,{...finding,nodeId:crypto.randomUUID()})).toThrow("outside");
    expect(()=>create(project,scene,sample,{...finding,proposedRepair:""})).toThrow("empty");
  });
  it("invalidates a source-bound draft after a commit, switch, or missing object",()=>{
    const {project,scene,plan,sample,finding}=checked();
    const draft=createNativeReviewRepairDraft(project,scene,plan,sample,finding);
    expect(nativeRepairDraftIsCurrent({...project,revision:project.revision+1},scene,draft,plan.profileId)).toBe(false);
    expect(nativeRepairDraftIsCurrent({...project,generation:crypto.randomUUID()},scene,draft,plan.profileId)).toBe(false);
    expect(nativeRepairDraftIsCurrent({...project,id:crypto.randomUUID()},scene,draft,plan.profileId)).toBe(false);
    expect(nativeRepairDraftIsCurrent(project,null,draft,plan.profileId)).toBe(false);
    expect(nativeRepairDraftIsCurrent(project,scene,draft,null)).toBe(false);
    expect(nativeRepairDraftIsCurrent(project,scene,draft,crypto.randomUUID())).toBe(false);
    expect(nativeRepairDraftIsCurrent(project,{...scene,nodes:[]},draft,plan.profileId)).toBe(false);
    expect(nativeRepairDraftIsCurrent(project,scene,{...draft,frameSha256:"corrupt"},plan.profileId)).toBe(false);
  });
});
