import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import {
  EVALUATOR_SCHEMA, evaluatorDigest, validateEvaluator, observeCriterion
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
