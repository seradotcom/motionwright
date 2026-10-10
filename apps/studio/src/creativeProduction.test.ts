import { describe, expect, it } from "vitest";
import { applyChange, bootstrap, previewCreativePatch } from "./api";
import { defaultHeroConfig, heroNodeId, mergeComponentNodes, planContentDigest, realizeProductHero } from "./creativeProduction";
import type { CreativePatch, ProductionPlan } from "./creativeProduction";
import { easedProgress, previewTransform } from "./canvasMotion";
import { fixtureProject } from "./fixture";
import { rationalSeconds, seconds } from "./types";
import type { Project } from "./types";

const instance = "00000000-0000-4000-8000-000000000005";
function source(): Project {
  const p = structuredClone(fixtureProject);
  p.locks=[]; p.branch_workspaces=[]; p.production_design={plan:null,heroes:[],capsules:[]};
  p.scenes=[{...p.scenes[0],nodes:[],beats:[],start:rationalSeconds(0),duration:rationalSeconds(6),renderer:"motion-canvas",status:"draft"}];
  p.reviews=[]; p.markers=[]; return p;
}
async function seeded() {
  const p=source();
  return applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:p.scenes[0].id,config:defaultHeroConfig()});
}

describe("first-party creative production",()=>{
  it("realizes stable component identities and exact bounded responsive output",async()=>{
    const landscape=await realizeProductHero(instance,defaultHeroConfig());
    expect(landscape).toHaveLength(6);
    expect(landscape.map(n=>n.name.split(" / ")[1])).toEqual(["eyebrow","headline","body","wordmark","rule","disclosure"]);
    for(const [w,h] of [[1920,1080],[1080,1920],[1080,1080]]){
      const nodes=await realizeProductHero(instance,defaultHeroConfig(),w,h);
      expect(nodes.map(n=>n.id)).toEqual(landscape.map(n=>n.id));
      for(const n of nodes){expect(n.x).toBeGreaterThanOrEqual(0);expect(n.y).toBeGreaterThanOrEqual(0);expect(n.x+n.width).toBeLessThanOrEqual(w);expect(n.y+n.height).toBeLessThanOrEqual(h);}
    }
    expect(landscape[0].id).toBe(await heroNodeId(instance,"eyebrow"));
    await expect(realizeProductHero(instance,{...defaultHeroConfig(),headline:"X".repeat(25)},1080,1920)).rejects.toThrow("wider");
  });
  it("reflows SplitExplanation with stable IDs and closes unsafe variation cases", async()=>{
    const original=await realizeProductHero(instance,defaultHeroConfig());
    const config={...defaultHeroConfig(), layout:"split_explanation" as const,
      headline:"First, the problem",body:"Then, explain the solution with original copy that can be edited.",wordmark:"DETAIL"};
    for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]){
      const nodes=await realizeProductHero(instance,config,width,height);
      expect(nodes.map(node=>node.id)).toEqual(original.map(node=>node.id));
      expect(nodes).toHaveLength(6);
      expect(nodes.every(node=>node.name.startsWith("SplitExplanation / "))).toBe(true);
      nodes.forEach(node=>{
        expect(node.x).toBeGreaterThanOrEqual(0); expect(node.y).toBeGreaterThanOrEqual(0);
        expect(node.x+node.width).toBeLessThanOrEqual(width); expect(node.y+node.height).toBeLessThanOrEqual(height);
      });
      const rule=nodes.find(node=>node.name.endsWith("rule"))!;
      expect(width>height?rule.height>rule.width:rule.width>rule.height).toBe(true);
      expect(nodes.at(-1)!.text).toContain("NOT A CAPTURE");
    }
    const legacy=structuredClone(defaultHeroConfig());
    delete legacy.layout;
    expect(await realizeProductHero(instance,legacy)).toEqual(original);
    await expect(realizeProductHero(instance,{...config,layout:"unknown" as never})).rejects.toThrow("Unsupported semantic");
  });
  it("switches families without resetting independent human text, rejects overlapping edits",async()=>{
    let project=await seeded();const scene_id=project.scenes[0].id;
    const eyebrow=await heroNodeId(instance,"eyebrow");
    const body=await heroNodeId(instance,"body");
    project=await applyChange(project,{type:"update_canvas_text",scene_id,node_id:eyebrow,text:"Edited by a human"});
    const before=project.revision;
    project=await applyChange(project,{type:"upsert_product_hero",instance_id:instance,scene_id,config:{...defaultHeroConfig(),layout:"split_explanation",wordmark:"DETAIL"}});
    expect(project.revision).toBe(before+1);
    expect(project.scenes[0].nodes.find(node=>node.id===eyebrow)?.text).toBe("Edited by a human");
    expect(project.scenes[0].nodes.find(node=>node.id===body)?.name).toBe("SplitExplanation / body");
    project=await applyChange(project,{type:"update_canvas_text",scene_id,node_id:body,text:"An independent human explanation"});
    const prior=structuredClone(project);
    await expect(applyChange(project,{type:"upsert_product_hero",instance_id:instance,scene_id,
      config:{...defaultHeroConfig(),layout:"split_explanation",wordmark:"DETAIL",body:"An incompatible new claim"}})).rejects.toThrow("override conflict");
    expect(project).toEqual(prior);
  });
  it("samples hold and exact out-cubic entrances without changing old smoothstep semantics",async()=>{
    expect(easedProgress(.25,"ease_out_cubic")).toBe(.578125);
    expect(easedProgress(.25,"ease_in_out")).toBe(.15625);
    const nodes=await realizeProductHero(instance,defaultHeroConfig());
    const mark=nodes.find(n=>n.name.endsWith("wordmark"))!;
    expect(previewTransform(mark,0).opacity).toBe(0);
    expect(previewTransform(mark,.16).rotation_deg).toBe(-5);
    expect(previewTransform(mark,.81).opacity).toBeCloseTo(.875,12);
    expect(previewTransform(mark,.81).rotation_deg).toBeCloseTo(-.625,12);
    expect(previewTransform(mark,2)).toMatchObject({y:mark.y,rotation_deg:0,opacity:1});
  });
  it("preserves a human override through ten agent-style revisions and serialization",async()=>{
    let p=await seeded(); const sceneId=p.scenes[0].id, body=await heroNodeId(instance,"body");
    expect(p.scenes[0].nodes[0]).not.toBe(p.production_design!.heroes[0].baseline[0]);
    p=await applyChange(p,{type:"update_canvas_text",scene_id:sceneId,node_id:body,text:"A deliberate human line."});
    for(let revision=1;revision<=10;revision++){
      p=await applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:sceneId,config:{...defaultHeroConfig(),eyebrow:`CREATIVE PRODUCTION / REVISION ${revision}`}});
      p=JSON.parse(JSON.stringify(p)) as Project;
      expect(p.scenes[0].nodes.find(n=>n.id===body)?.text).toBe("A deliberate human line.");
      expect(p.production_design?.heroes[0].id).toBe(instance);
      expect(p.scenes[0].nodes).toHaveLength(6);
    }
  });
  it("rejects conflicts and locks atomically rather than erasing a human's work",async()=>{
    let p=await seeded(); const body=await heroNodeId(instance,"body");
    p=await applyChange(p,{type:"update_canvas_text",scene_id:p.scenes[0].id,node_id:body,text:"Human copy"});
    const before=structuredClone(p);
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:p.scenes[0].id,config:{...defaultHeroConfig(),body:"Agent copy"}})).rejects.toThrow("override conflict");
    expect(p).toEqual(before);
    const mark=await heroNodeId(instance,"wordmark");
    p=await applyChange(p,{type:"set_node_property_lock",scene_id:p.scenes[0].id,node_id:mark,property:"style",locked:true});
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id:p.scenes[0].id,config:{...defaultHeroConfig(),accent:"#AABBCC"}})).rejects.toThrow("locked");
  });
  it("does not mutate browser storage, current project or revision during patch inspection",async()=>{
    const p=await seeded(), body=await heroNodeId(instance,"body"), before=await bootstrap();
    const patch:CreativePatch={scene_id:p.scenes[0].id,base_revision:p.revision,rationale:"Shorten the line without touching the rest of the composition",edits:[{kind:"text",node_id:body,text:"A shorter line."}]};
    const preview=await previewCreativePatch(p,patch);
    expect(await bootstrap()).toEqual(before);
    expect(preview.before[0].text).not.toBe(preview.after[0].text);
    expect(preview.after[0].text).toBe("A shorter line.");
    expect(seconds(preview.dirty_end)).toBe(6);
    expect(preview.kind).toContain("not_pixel_verification");
    const applied=await applyChange(p,{type:"apply_creative_patch",patch});
    expect(applied.revision).toBe(p.revision+1);
    expect(applied.scenes[0].nodes.filter(n=>n.id!==body)).toEqual(p.scenes[0].nodes.filter(n=>n.id!==body));
    await expect(applyChange(applied,{type:"apply_creative_patch",patch})).rejects.toThrow("stale");
  });
  it("keeps multiple patch edits atomic and enforces per-property locks",async()=>{
    let p=await seeded();const scene_id=p.scenes[0].id,body=await heroNodeId(instance,"body"),mark=await heroNodeId(instance,"wordmark");
    p=await applyChange(p,{type:"set_node_property_lock",scene_id,node_id:mark,property:"text",locked:true});
    const before=await bootstrap();
    const patch:CreativePatch={scene_id,base_revision:p.revision,rationale:"A proposal that must fail as a unit",edits:[{kind:"text",node_id:body,text:"Should never be stored"},{kind:"text",node_id:mark,text:"Locked"}]};
    await expect(applyChange(p,{type:"apply_creative_patch",patch})).rejects.toThrow("locked");
    expect(await bootstrap()).toEqual(before);
    await expect(previewCreativePatch(p,{...patch,edits:[{kind:"text",node_id:crypto.randomUUID(),text:"Outside scope"}]})).rejects.toThrow("outside");
  });
  it("preserves node deletion and offers explicit detach without deleting authored objects",async()=>{
    let p=await seeded();const baseline=p.production_design!.heroes[0].baseline;
    expect(()=>mergeComponentNodes(baseline,p.scenes[0].nodes.slice(1),baseline)).toThrow("deleted");
    const nodes=structuredClone(p.scenes[0].nodes);
    p=await applyChange(p,{type:"detach_product_hero",instance_id:instance});
    expect(p.production_design?.heroes).toHaveLength(0);expect(p.scenes[0].nodes).toEqual(nodes);
  });
  it("keeps plan approval tied to its current content and requires actual source assets for capture claims",async()=>{
    let p=await seeded();const plan:ProductionPlan={objective:"Explain the workflow",audience:"Creative teams",concept:"Typography carries a truthful argument",reference_constraints:[],exclusions:["No fake product screenshots"],shots:[{scene_id:p.scenes[0].id,purpose:"Open the argument",claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"}],clock:{kind:"timeline"},approval:null};
    const digest=await planContentDigest(plan);
    p=await applyChange(p,{type:"set_production_plan",plan:{...plan,approval:{reviewer:"Owner",note:"Approved concept, not a fidelity pass",content_sha256:digest}}});
    await expect(applyChange(p,{type:"set_production_plan",plan:{...p.production_design!.plan!,concept:"Changed concept"}})).rejects.toThrow("stale");
    await expect(applyChange(p,{type:"set_production_plan",plan:{...plan,shots:[{...plan.shots[0],evidence_kind:"real_capture"}]}})).rejects.toThrow("digest-bound");
  });
});


describe("MetricEvidence as a third versioned composition family", () => {
  const config = {
    ...defaultHeroConfig(), layout:"metric_evidence" as const,
    eyebrow:"USER AUTHORED / NOT VERIFIED",
    headline:"42% growth",
    body:"A reported figure needs a source, date and independently checked evidence.",
    wordmark:"DATA",accent:"#D9A46E",
  };

  it("reflows in three aspects with stable semantic IDs and a persistent unverified disclosure", async () => {
    const original=await realizeProductHero(instance,defaultHeroConfig());
    for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]) {
      const nodes=await realizeProductHero(instance,config,width,height);
      expect(nodes).toHaveLength(6);
      expect(nodes.map(n=>n.id)).toEqual(original.map(n=>n.id));
      for(const node of nodes) {
        expect(node.name.startsWith("MetricEvidence / ")).toBe(true);
        expect(node.x).toBeGreaterThanOrEqual(0);
        expect(node.y).toBeGreaterThanOrEqual(0);
        expect(node.x+node.width).toBeLessThanOrEqual(width);
        expect(node.y+node.height).toBeLessThanOrEqual(height);
      }
      const rule=nodes.find(n=>n.name.endsWith("rule"))!;
      expect(width>height?rule.height>rule.width:rule.width>rule.height).toBe(true);
      expect(nodes.find(n=>n.name.endsWith("headline"))).toMatchObject({text:"42% growth",style:{fill:"#D9A46E"}});
      expect(nodes.at(-1)?.text).toBe("METRIC EVIDENCE / EDITORIAL STUDY · SOURCE NOT VERIFIED");
    }
    const staticNodes=await realizeProductHero(instance,{...config,motion:false});
    expect(staticNodes.every(n=>n.keyframes.length===0)).toBe(true);
  });

  it("protects human source and explanations while switching families", async () => {
    let p=await seeded();const scene_id=p.scenes[0].id;
    const eyebrow=await heroNodeId(instance,"eyebrow");
    const body=await heroNodeId(instance,"body");
    p=await applyChange(p,{type:"update_canvas_text",scene_id,node_id:eyebrow,text:"Human-authored source context"});
    p=await applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id,config});
    expect(p.scenes[0].nodes.find(n=>n.id===eyebrow)?.text).toBe("Human-authored source context");
    expect(p.scenes[0].nodes.find(n=>n.id===body)?.name).toBe("MetricEvidence / body");
    p=await applyChange(p,{type:"update_canvas_text",scene_id,node_id:body,text:"Human-controlled explanation"});
    const before=structuredClone(p);
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:instance,scene_id,
      config:{...config,body:"An incompatible regenerated metric claim"}})).rejects.toThrow("override conflict");
    expect(p).toEqual(before);
  });
});
