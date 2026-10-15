import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import {EVALUATOR_SCHEMA,type CreativeEvaluatorBundle} from "./creativeEvaluator";
import {
  EVALUATOR_EXPERIMENT_SCHEMA,previewEvaluatorExperiment,revalidateEvaluatorExperiment,
} from "./evaluatorExperiment";
import type {Project} from "./types";

const id="00000000-0000-4000-8000-000000000077";
const nodeId="00000000-0000-4000-8000-000000000078";
const humanId="00000000-0000-4000-8000-000000000079";
const textId="00000000-0000-4000-8000-000000000080";

const baseline=():CreativeEvaluatorBundle=>({
  schema:EVALUATOR_SCHEMA,id,version:1,label:"Original source-only technical experiment",
  purpose:"Compare source-authored structures without creative scoring",
  author_attestation:"I authored and own the data-only source evaluator",
  playbook:["Inspect source-authored editable nodes","Ask an independent human before quality claims"],
  criteria:[
    {kind:"minimum_nodes",id:nodeId,minimum:2},
    {kind:"text_contains",id:textId,phrase:"AI agents"},
    {kind:"human_question",id:humanId,question:"Does the scene communicate the point to viewers?"},
  ],
});
function fixture():Project{
  const p=structuredClone(fixtureProject);
  p.production_design=emptyProductionDesign();
  p.production_design.evaluator_bundles=[baseline()];
  p.locks=[];p.branch_workspaces=[];
  return p;
}
function candidate(change:Partial<CreativeEvaluatorBundle>={}):CreativeEvaluatorBundle{
  return {...baseline(),version:2,
    criteria:baseline().criteria.map(c=>c.kind==="minimum_nodes"?{...c,minimum:3}:c),
    ...change};
}
const call=async(p:Project,body=candidate(),ids=p.scenes.slice(0,2).map(s=>s.id))=>
  previewEvaluatorExperiment(p,id,1,JSON.stringify(body),ids);

describe("E13 bounded first-party, data-only evaluator experiment",()=>{
  it("measures real current scene differences and never calls an external skill or makes a project revision",async()=>{
    const p=fixture(),snapshot=structuredClone(p);
    const observed=await call(p);
    expect(observed.schema).toBe(EVALUATOR_EXPERIMENT_SCHEMA);
    expect(observed.status).toBe("TECHNICAL_OBSERVATION_ONLY");
    expect(observed.comparisons).toHaveLength(2);
    expect(observed.comparisons.map(item=>item.matchingCriterionRegressions)).toEqual([1,1]);
    expect(observed.comparisons.every(item=>item.candidateAbstain>=1)).toBe(true);
    expect(observed.humanCreativeApproval).toBe("not_assessed");
    expect(observed.executableCodeRun).toBe(false);
    expect(observed.evaluatorInstalled).toBe(false);
    expect(observed.rightsAuthenticated).toBe(false);
    expect(observed.providerCalled).toBe(false);
    expect(observed.baselineSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(observed.candidateSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(observed.baselineSha256).not.toBe(observed.candidateSha256);
    expect(p).toEqual(snapshot);
    expect(await revalidateEvaluatorExperiment(p,observed)).toEqual(candidate());
  });
  it("refuses a version-only bump rather than fabricate a meaningful experiment",async()=>{
    await expect(call(fixture(),{...baseline(),version:2})).rejects.toThrow(/must change/);
  });
  it("requires the latest original version and exact candidate ID/next version",async()=>{
    const p=fixture();
    await expect(call(p,candidate({id:"00000000-0000-4000-8000-000000000099"}))).rejects.toThrow(/same evaluator/);
    await expect(call(p,candidate({version:4}))).rejects.toThrow(/increment/);
    p.production_design!.evaluator_bundles!.push(candidate());
    await expect(call(p,candidate({version:3}))).rejects.toThrow(/latest immutable/);
  });
  it("never accepts executable JSON fields or removal of human-review abstention",async()=>{
    await expect(call(fixture(),{...candidate(),runtime:"curl unsafe"} as unknown as CreativeEvaluatorBundle))
      .rejects.toThrow(/fields/);
    await expect(call(fixture(),candidate({criteria:[{kind:"minimum_nodes",id:nodeId,minimum:3}]})))
      .rejects.toThrow(/human creative/);
  });
  it("refuses foreign, duplicate or excessive source scene sets",async()=>{
    const p=fixture(),ids=p.scenes.slice(0,3).map(scene=>scene.id);
    await expect(call(p,candidate(),[ids[0],ids[0]])).rejects.toThrow(/distinct/);
    await expect(call(p,candidate(),["00000000-0000-4000-8000-000000000999"])).rejects.toThrow(/external/);
    await expect(call(p,candidate(),[...ids,p.scenes[3].id])).rejects.toThrow(/one to three/);
    expect((await call(p,candidate(),ids)).comparisons).toHaveLength(3);
  });
  it("records removed/added criteria rather than treating deletion of failures as improvement",async()=>{
    const p=fixture(),body=candidate();
    body.criteria=body.criteria.filter(c=>c.id!==textId);
    body.criteria.push({kind:"project_pixel_safe_area",id:"00000000-0000-4000-8000-000000000081",margin_permille:75});
    const report=await call(p,body);
    expect(report.removedCriterionIds).toEqual([textId]);
    expect(report.addedCriterionIds).toEqual(["00000000-0000-4000-8000-000000000081"]);
    expect(report.comparisons.every(scene=>scene.candidateAbstain>=1)).toBe(true);
  });
  it("rejects a content lock, oversized payload and malformed evaluator source",async()=>{
    const p=fixture();p.locks.push({id:"lock",resource:"project:"+p.id,kind:"content",note:"Owner lock"});
    await expect(call(p)).rejects.toThrow(/locked/);
    p.locks=[];
    await expect(previewEvaluatorExperiment(p,id,1," ".repeat(17000),[p.scenes[0].id]))
      .rejects.toThrow(/budget/);
    await expect(previewEvaluatorExperiment(p,id,1,"{not-json}",[p.scenes[0].id]))
      .rejects.toThrow(/not valid JSON/);
  });
  it("rechecks project generation/revision, source recipe, observations and truth flags before admission",async()=>{
    const p=fixture(),result=await call(p);
    p.revision++;
    await expect(revalidateEvaluatorExperiment(p,result)).rejects.toThrow(/stale/);
    p.revision--;
    const forged=structuredClone(result);
    forged.comparisons[0].candidatePass=999;
    await expect(revalidateEvaluatorExperiment(p,forged)).rejects.toThrow(/changed/);
    // Deliberately emulate malformed untrusted JSON without broadening the product's literal-false type.
    const forgedFlags={...structuredClone(result),evaluatorInstalled:true} as unknown as typeof result;
    await expect(revalidateEvaluatorExperiment(p,forgedFlags)).rejects.toThrow(/ungranted/);
  });
});
