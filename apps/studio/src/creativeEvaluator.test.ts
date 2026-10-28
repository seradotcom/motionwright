import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import {
  EVALUATOR_SCHEMA, evaluatorDigest, validateEvaluator, observeCriterion,
  observeStyleAdvisories, validateEvaluatorState
} from "./creativeEvaluator";
import type { CreativeEvaluatorBundle, CreativeCriterion } from "./creativeEvaluator";
import type { Project } from "./types";

const id="00000000-0000-4000-8000-000000000007";
const nodeId="00000000-0000-4000-8000-000000000008";
const reviewId="00000000-0000-4000-8000-000000000009";
const textId="00000000-0000-4000-8000-000000000010";
function bundle(version=1):CreativeEvaluatorBundle{
  return {
    schema:EVALUATOR_SCHEMA,id,version,label:"Source-original evaluator",
    purpose:"Verify authored Canvas without aesthetic scoring",
    author_attestation:"I authored and may publish the original evaluation criteria",
    playbook:["Inspect native-editable source objects","Request independent human review"],
    criteria:[
      {kind:"minimum_nodes",id:nodeId,minimum:2},
      {kind:"text_contains",id:textId,phrase:"Original claim"},
      {kind:"human_question",id:reviewId,question:"Is this creative direction clear?"},
    ],
  };
}
function project():Project{
  const p=structuredClone(fixtureProject);
  p.production_design=emptyProductionDesign();
  p.locks=[];
  p.branch_workspaces=[];
  p.reviews=[];
  p.scenes=p.scenes.slice(0,1).map(scene=>({...scene,status:"draft" as const,
    renderer:"motion-canvas" as const,beats:[],
    nodes:[
      {...structuredClone(fixtureProject.scenes[0].nodes[0]),
        id:crypto.randomUUID(),kind:"text",text:"Original claim as an editable subject",
        x:260,y:210,width:550,height:90,coordinate_space:"project_pixels" as const},
      {...structuredClone(fixtureProject.scenes[0].nodes[0]),
        id:crypto.randomUUID(),kind:"text",text:"Source-grounded supporting detail",
        x:260,y:380,width:550,height:90,coordinate_space:"project_pixels" as const},
    ],
  }));
  return p;
}
describe("versioned data-only scene evaluator",()=>{
  it("matches canonical Rust SHA-256",async()=>{
    expect(await evaluatorDigest(bundle())).toBe(
      "a060f3356f26c535f6bc14742ca7769541efc88827fb5443a54e033b22694056");
  });
  it("records a revision-bound technical observation and abstains on human aesthetic judgment",async()=>{
    let p=project();
    const revision=p.revision;
    const source=bundle();
    p=await applyChange(p,{type:"register_creative_evaluator",bundle:source});
    const evaluatedRevision=p.revision;
    p=await applyChange(p,{type:"evaluate_creative_scene",
      evaluator_id:id,evaluator_version:1,scene_id:p.scenes[0].id});
    const report=p.production_design?.evaluation_reports?.[0];
    expect(report?.source_revision).toBe(evaluatedRevision);
    expect(report?.source_revision).toBeGreaterThan(revision);
    expect(report?.evaluator_sha256).toBe(await evaluatorDigest(source));
    expect(report?.conclusion).toBe("needs_human_review");
    expect(report?.creative_approval).toBe("not_assessed");
    expect(report?.observations.map(o=>o.outcome)).toEqual(["pass","pass","not_assessed"]);
    expect(p.production_design?.evaluator_bundles).toEqual([source]);
    expect(JSON.parse(JSON.stringify(p)).production_design.evaluation_reports).toHaveLength(1);
  });
  it("preserves immutable source versions and historical reports when original Canvas changes",async()=>{
    let p=project();
    const first=bundle(1);
    p=await applyChange(p,{type:"register_creative_evaluator",bundle:first});
    p=await applyChange(p,{type:"evaluate_creative_scene",
      evaluator_id:id,evaluator_version:1,scene_id:p.scenes[0].id});
    const historical=structuredClone(p.production_design?.evaluation_reports?.[0]);
    const versionTwo={...bundle(2),playbook:["Another explicitly versioned original recipe"]};
    p=await applyChange(p,{type:"register_creative_evaluator",bundle:versionTwo});
    expect(p.production_design?.evaluator_bundles).toHaveLength(2);
    await expect(applyChange(p,{type:"register_creative_evaluator",bundle:versionTwo}))
      .rejects.toThrow(/append-only/);
    p=await applyChange(p,{type:"update_canvas_text",scene_id:p.scenes[0].id,
      node_id:p.scenes[0].nodes[0].id,text:"New authored copy"});
    p=await applyChange(p,{type:"evaluate_creative_scene",
      evaluator_id:id,evaluator_version:1,scene_id:p.scenes[0].id});
    expect(p.production_design?.evaluation_reports?.[0]).toEqual(historical);
    expect(p.production_design?.evaluation_reports?.[1].conclusion).toBe("fail");
    expect(p.production_design?.evaluation_reports?.[1].observations[1].outcome).toBe("fail");
  });
  it("rejects executable fields, duplicate criteria and fraudulent creative approval",async()=>{
    const first=bundle();
    expect(()=>validateEvaluator({...first,execute_shell:"unsafe"} as unknown as CreativeEvaluatorBundle))
      .toThrow(/schema/);
    const repeated={...first,criteria:[first.criteria[0],{...first.criteria[1],id:first.criteria[0].id}]} as CreativeEvaluatorBundle;
    expect(()=>validateEvaluator(repeated)).toThrow(/unique/);
    let p=await applyChange(project(),{type:"register_creative_evaluator",bundle:first});
    p=await applyChange(p,{type:"evaluate_creative_scene",
      evaluator_id:id,evaluator_version:1,scene_id:p.scenes[0].id});
    p.production_design!.evaluation_reports![0].creative_approval="approved";
    await expect(applyChange(p,{type:"register_creative_evaluator",bundle:{
      ...first,id:crypto.randomUUID(),
    }})).rejects.toThrow(/Invalid source-bound/);
  });
  it("fails outside-safe-area geometry and abstains on unknown coordinate spaces",()=>{
    const scene=project().scenes[0];
    const criterion:CreativeCriterion={kind:"project_pixel_safe_area",id:crypto.randomUUID(),margin_permille:50};
    expect(observeCriterion(scene,criterion).outcome).toBe("pass");
    scene.nodes[0].x=0;
    expect(observeCriterion(scene,criterion).outcome).toBe("fail");
    scene.nodes[0].coordinate_space="relative_parent" as typeof scene.nodes[0]["coordinate_space"];
    expect(observeCriterion(scene,criterion).outcome).toBe("not_assessed");
  });
});

describe("source-bound style advisory acceptance without aesthetic automation",()=>{
  it("identifies exact repeated text, authored overdraw and motion-purpose review candidates",async()=>{
    let p=project();
    const scene=p.scenes[0];
    const original=structuredClone(scene.nodes[0]);
    scene.nodes[0].text="Original claim repeated exactly";
    for(let index=1;index<3;index++){
      scene.nodes.push({...structuredClone(scene.nodes[0]),id:crypto.randomUUID(),
        name:"Repeated first-party text "+index,x:290+index*75});
    }
    for(let index=0;index<4;index++){
      scene.nodes.push({...structuredClone(original),id:crypto.randomUUID(),
        name:"Dense original rectangle "+index,kind:"rectangle",text:null,
        x:200+index*40,y:100+index*20,width:1200,height:800,keyframes:[]});
    }
    scene.nodes[0].keyframes=[{at:{num:"1",den:"1"},property:"x",
      value:scene.nodes[0].x+200,interpolation:"linear"}];
    const candidates=observeStyleAdvisories(scene);
    expect(candidates.map(x=>x.kind)).toEqual(expect.arrayContaining([
      "repetition","density","motion_purpose"]));
    expect(candidates.every(value=>value.scene_id===scene.id&&value.human_review_required
      &&value.source_node_ids.length>0&&value.rationale&&value.suggested_action)).toBe(true);
    const motion=candidates.find(value=>value.kind==="motion_purpose")!;
    expect(motion.interval_start).toEqual({num:"0",den:"1"});
    expect(motion.interval_end).toEqual({num:"1",den:"1"});
    p=await applyChange(p,{type:"register_creative_evaluator",bundle:bundle()});
    const revision=p.revision;
    p=await applyChange(p,{type:"evaluate_creative_scene",scene_id:scene.id,
      evaluator_id:id,evaluator_version:1});
    const report=p.production_design!.evaluation_reports!.at(-1)!;
    expect(report.style_advisories).toEqual(candidates);
    expect(report.source_revision).toBe(revision);
    expect(report.creative_approval).toBe("not_assessed");
    expect(report.conclusion).toBe("needs_human_review");
    await expect(validateEvaluatorState(p)).resolves.toBeUndefined();
    const forged=structuredClone(p);
    forged.production_design!.evaluation_reports!.at(-1)!.style_advisories![0].human_review_required=false;
    await expect(validateEvaluatorState(forged)).rejects.toThrow(/advisory|reviewer/i);
  });
  it("keeps quiet on sparse distinct static content and accepts historical report JSON",async()=>{
    let p=project();expect(observeStyleAdvisories(p.scenes[0])).toEqual([]);
    p=await applyChange(p,{type:"register_creative_evaluator",bundle:bundle()});
    p=await applyChange(p,{type:"evaluate_creative_scene",scene_id:p.scenes[0].id,
      evaluator_id:id,evaluator_version:1});
    const report=p.production_design!.evaluation_reports!.at(-1)!;
    expect(Object.hasOwn(report,"style_advisories")).toBe(false);
    expect(JSON.parse(JSON.stringify(report)).style_advisories).toBeUndefined();
    await expect(validateEvaluatorState(p)).resolves.toBeUndefined();
  });
});
