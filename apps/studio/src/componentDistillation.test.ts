import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { emptyProductionDesign } from "./creativeProduction";
import { fixtureProject } from "./fixture";
import type { CanvasNode, Project } from "./types";
import type { ComponentDistillationInput, DistillationReview } from "./componentDistillation";
import { DISTILLATION_RENDERER, DISTILLATION_SCHEMA } from "./componentDistillation";

function node(kind:string,text:string):CanvasNode {
  return {
    ...structuredClone(fixtureProject.scenes[0].nodes[0]),
    id:crypto.randomUUID(),name:"Authored "+kind,kind,
    parent_id:null,x:290,y:250,width:480,height:96,rotation_deg:0,opacity:1,
    text:kind==="text"?text:null,relations:[],property_locks:[],
    keyframes:[],z_index:0,
  };
}
function seed():{p:Project;input:ComponentDistillationInput;negativeId:string}{
  const p=structuredClone(fixtureProject);
  p.schema_version=2;p.locks=[];p.reviews=[];p.merges=[];p.branch_workspaces=[];
  p.production_design=emptyProductionDesign();
  p.scenes=p.scenes.slice(0,3).map(scene=>({
    ...scene,renderer:"motion-canvas" as const,
    nodes:[],beats:[],status:"draft" as const,
  }));
  const positives = p.scenes.slice(0,2).map((scene,index)=>{
    const rect=node("rectangle","Background");
    const text=node("text",index===0?"Sign in":"Continue");
    scene.nodes=[rect,text];
    return {scene_id:scene.id,node_ids:[rect.id,text.id]};
  });
  const locked=node("text","Protected original");
  locked.property_locks=["text"];
  p.scenes[2].nodes=[locked];
  return {
    p,negativeId:locked.id,
    input:{
      id:crypto.randomUUID(),label:"Original editable text panel",
      author:"Original source author",
      original_rights_attestation:"This is original editable artwork that I may propose for reuse.",
      source_project_id:p.id,source_generation:p.generation,
      source_revision:p.revision,
      roles:[{key:"panel",kind:"rectangle"},{key:"headline",kind:"text"}],
      positives,
      negatives:[{scene_id:p.scenes[2].id,node_id:locked.id,rejection:"locked_object"}],
      requirements:["Editable original typography with stable source objects",
        "Respect human source locks and counterexamples"],
    },
  };
}
const propose=(p:Project,input:ComponentDistillationInput)=>applyChange(p,{
  type:"propose_component_distillation",input,
});
describe("validated component distillation source proposals",()=>{
  it("records only source-owned positive variations and an actually locked counterexample",async()=>{
    const {p,input}=seed();
    const before=structuredClone(p.scenes);
    const candidate=await propose(p,input);
    expect(candidate.scenes).toEqual(before);
    expect(candidate.production_design?.distillations).toHaveLength(1);
    const proposal=candidate.production_design?.distillations?.[0]!;
    expect(proposal.schema).toBe(DISTILLATION_SCHEMA);
    expect(proposal.samples.map(s=>s.nodes[1].text)).toEqual(["Sign in","Continue"]);
    expect(proposal.input.negatives).toEqual(input.negatives);
    expect(proposal.review).toBeNull();
    expect(candidate.production_design?.heroes).toEqual([]);
    expect(candidate.production_design?.capsules).toEqual([]);
    const changed=await applyChange(candidate,{
      type:"update_canvas_text",scene_id:input.positives[0].scene_id,
      node_id:input.positives[0].node_ids[1],text:"Later source change",
    });
    expect(changed.production_design?.distillations?.[0].samples[0].nodes[1].text).toBe("Sign in");
    expect(JSON.parse(JSON.stringify(changed)).production_design.distillations).toHaveLength(1);
  });
  it("requires an independent source-bound reviewer and never performs an installation",async()=>{
    const {p,input}=seed();
    const candidate=await propose(p,input);
    const review:DistillationReview={
      reviewer:"Independent craft reviewer",verdict:"compatible_proposal",
      rationale:"Source variations are structurally compatible for a later human admission review.",
      source_revision:input.source_revision,compatibility:DISTILLATION_RENDERER,
    };
    const after=await applyChange(candidate,{type:"review_component_distillation",
      id:input.id,review});
    expect(after.production_design?.distillations?.[0].review).toEqual(review);
    expect(after.production_design?.heroes).toHaveLength(0);
    expect(after.production_design?.capsules).toHaveLength(0);
    await expect(applyChange(after,{type:"review_component_distillation",id:input.id,review}))
      .rejects.toThrow(/reviewed only once|reviewed/);
    await expect(applyChange(candidate,{type:"review_component_distillation",id:input.id,
      review:{...review,reviewer:input.author}})).rejects.toThrow(/independently/);
    await expect(applyChange(candidate,{type:"review_component_distillation",id:input.id,
      review:{...review,compatibility:"install_and_execute_code"}})).rejects.toThrow(/review/);
  });
  it("rejects stale and foreign sources, absent variation, counterfeit negatives and unknown references",async()=>{
    const {p,input,negativeId}=seed();
    const variants:ComponentDistillationInput[]=[
      {...input,source_project_id:crypto.randomUUID()},
      {...input,source_generation:crypto.randomUUID()},
      {...input,source_revision:input.source_revision+1},
      {...input,positives:[input.positives[0],input.positives[0]]},
      {...input,roles:[input.roles[0],input.roles[0]]},
      {...input,negatives:[{scene_id:p.scenes[2].id,node_id:negativeId,
        rejection:"unsupported_kind"}]},
      {...input,original_rights_attestation:""},
    ];
    for(const proposal of variants)
      await expect(propose(p,proposal)).rejects.toThrow();
    const clone=structuredClone(p);
    clone.scenes[1].nodes[1].text="Sign in";
    await expect(propose(clone,input)).rejects.toThrow(/variation/);
    expect(p.production_design?.distillations).toEqual([]);
    const untrusted={...input,execute_source:"npm install unknown-skill"};
    await expect(propose(p,untrusted as ComponentDistillationInput))
      .rejects.toThrow(/schema fields/);
  });
});
