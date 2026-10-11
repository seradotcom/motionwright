import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { defaultHeroConfig, emptyProductionDesign, heroNodeId } from "./creativeProduction";
import { brandDigest } from "./brandGovernance";
import type { BrandProfile, BrandException } from "./brandGovernance";
import { fixtureProject } from "./fixture";
import { rationalSeconds } from "./types";
import type { Project } from "./types";
const identity="00000000-0000-4000-8000-000000000005";
const ruleId="00000000-0000-4000-8000-000000000006";
async function seeded():Promise<Project> {
  let p=structuredClone(fixtureProject);
  p.locks=[];p.branch_workspaces=[];p.production_design=emptyProductionDesign();
  p.scenes=p.scenes.slice(0,2).map((s,i)=>({...s,nodes:[],beats:[],start:rationalSeconds(i*6),
    duration:rationalSeconds(6),renderer:"motion-canvas",status:"draft"}));
  p.reviews=[];p.markers=[];
  return applyChange(p,{type:"upsert_product_hero",instance_id:identity,
    scene_id:p.scenes[0].id,config:defaultHeroConfig()});
}
function brand(phrase:string):BrandProfile {
  return {id:"00000000-0000-4000-8000-000000000011",label:"The brand",version:1,
    rules:[{kind:"forbidden_phrase",id:ruleId,phrase}]};
}
describe("brand, taste, decisions and campaign scope",()=>{
  it("rejects forbidden copy even if inferred taste prefers it",async()=>{
    let p=await seeded();
    p=await applyChange(p,{type:"set_brand_governance",profile:brand("unverified"),exceptions:[]});
    p=await applyChange(p,{type:"set_taste_profile",profile:{id:crypto.randomUUID(),label:"Agent taste",
      preferences:[{axis:"tone",preference:"Say unverified",inferred:true}]}});
    await expect(applyChange(p,{type:"update_canvas_text",scene_id:p.scenes[0].id,
      node_id:await heroNodeId(identity,"headline"),text:"An unverified claim"}))
      .rejects.toThrow("Brand rule");
    expect(p.production_design?.brand_profile?.rules).toHaveLength(1);
  });
  it("limits a waiver to the exact policy digest, rule and scene",async()=>{
    let p=await seeded();
    const profile=brand("special");
    p=await applyChange(p,{type:"set_brand_governance",profile,exceptions:[]});
    const waiver:BrandException={id:crypto.randomUUID(),rule_id:ruleId,scene_id:p.scenes[0].id,
      brand_sha256:await brandDigest(profile),campaign:"Bounded pilot",author:"Recorded producer",
      rationale:"A specifically authorized quote"};
    p=await applyChange(p,{type:"set_brand_governance",profile,exceptions:[waiver]});
    p=await applyChange(p,{type:"update_canvas_text",scene_id:p.scenes[0].id,
      node_id:await heroNodeId(identity,"headline"),text:"A special quote"});
    const revised={...profile,version:2};
    await expect(applyChange(p,{type:"set_brand_governance",profile:revised,exceptions:[waiver]}))
      .rejects.toThrow("stale policy digest");
    await expect(applyChange(p,{type:"set_brand_governance",profile:{...profile,rules:[]},exceptions:[waiver]}))
      .rejects.toThrow("increase version");
    const d=JSON.parse(JSON.stringify(p));
    expect(d.production_design.brand_exceptions[0].scene_id).toBe(p.scenes[0].id);
  });
  it("never extends a scene waiver to another hero or creates a global exception",async()=>{
    let p=await seeded();const policy=brand("special");
    const waiver:BrandException={id:crypto.randomUUID(),rule_id:ruleId,scene_id:p.scenes[0].id,
      brand_sha256:await brandDigest(policy),campaign:"Specific scene",author:"Producer",
      rationale:"Only this scene"};
    p=await applyChange(p,{type:"set_brand_governance",profile:policy,exceptions:[waiver]});
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:crypto.randomUUID(),
      scene_id:p.scenes[1].id,config:{...defaultHeroConfig(),headline:"Special treatment"}}))
      .rejects.toThrow("Brand rule");
  });
  it("stores decisions separately without changing brand rules",async()=>{
    let p=await seeded();
    p=await applyChange(p,{type:"set_brand_governance",profile:brand("unverified"),exceptions:[]});
    p=await applyChange(p,{type:"record_creative_decision",decision:{
      id:crypto.randomUUID(),scene_id:p.scenes[0].id,author:"Editor",
      decision:"Try an editorial cadence",rationale:"Measured readability",
    }});
    expect(p.production_design?.project_decisions).toHaveLength(1);
    expect(p.production_design?.brand_profile?.rules[0].kind).toBe("forbidden_phrase");
  });
});

describe("MetricEvidence and BrandProfile share the exact accent authority",()=>{
  it("checks the accent-painted metric headline, preserves a rejected human edit and admits only a scoped waiver",async()=>{
    let p=await seeded();
    const scene_id=p.scenes[0].id;
    const config={...defaultHeroConfig(),layout:"metric_evidence" as const,
      headline:"42% growth",
      body:"A reported figure is not verified without its source.",
      wordmark:"DATA",accent:"#D9A46E"};
    p=await applyChange(p,{type:"upsert_product_hero",instance_id:identity,scene_id,config});
    const rule={kind:"allowed_accents" as const,id:ruleId,colors:["#D9A46E"]};
    const profile:BrandProfile={id:crypto.randomUUID(),label:"Evidence brand",version:1,
      rules:[rule,{kind:"required_wordmark",id:crypto.randomUUID(),text:"DATA"}]};
    p=await applyChange(p,{type:"set_brand_governance",profile,exceptions:[]});
    const targetId=await heroNodeId(identity,"headline");
    const node=p.scenes[0].nodes.find(n=>n.id===targetId)!;
    expect(node.style.fill).toBe("#D9A46E");
    const previous=structuredClone(p);
    const changed={...node.style,fill:"#EE0000"};
    await expect(applyChange(p,{type:"update_canvas_style",scene_id,node_id:targetId,style:changed}))
      .rejects.toThrow("Brand rule");
    expect(p).toEqual(previous);
    await expect(applyChange(p,{type:"update_canvas_style",scene_id,node_id:targetId,
      style:{...node.style,fill:null}})).rejects.toThrow("Brand rule");
    expect(p).toEqual(previous);

    const waiver:BrandException={id:crypto.randomUUID(),rule_id:ruleId,scene_id,
      brand_sha256:await brandDigest(profile),campaign:"Specific metric",
      author:"Recorded reviewer",rationale:"Intentional local accent variation"};
    p=await applyChange(p,{type:"set_brand_governance",profile,exceptions:[waiver]});
    p=await applyChange(p,{type:"update_canvas_style",scene_id,node_id:targetId,style:changed});
    expect(p.scenes[0].nodes.find(n=>n.id===targetId)?.style.fill).toBe("#EE0000");
    expect(p.production_design?.brand_exceptions).toHaveLength(1);
    const changedBrand={...profile,version:2};
    await expect(applyChange(p,{type:"set_brand_governance",profile:changedBrand,exceptions:[waiver]}))
      .rejects.toThrow("stale policy digest");
  });
});
