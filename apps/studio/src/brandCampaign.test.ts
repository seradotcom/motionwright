import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {defaultHeroConfig,emptyProductionDesign} from "./creativeProduction";
import {brandDigest} from "./brandGovernance";
import type {Project} from "./types";
import {
  BRAND_ROLLOUT_SCHEMA,previewBrandCampaign,validateBrandCampaignReceipt,
  type BrandCampaignRollout,
} from "./brandCampaign";

function original():Project{
  const p=structuredClone(fixtureProject);
  p.locks=[];p.reviews=[];p.scenes.forEach(s=>s.status="draft");
  p.production_design=emptyProductionDesign();
  p.production_design.heroes=p.scenes.slice(0,2).map(scene=>({
    id:crypto.randomUUID(),scene_id:scene.id,component_version:1,
    config:defaultHeroConfig(),baseline:[],
  }));
  p.production_design.brand_profile={
    id:crypto.randomUUID(),label:"Original brand",version:1,
    rules:[
      {kind:"allowed_accents",id:crypto.randomUUID(),colors:["#A5C8DF","#F5B84A"]},
      {kind:"required_wordmark",id:crypto.randomUUID(),text:"Mw"},
    ],
  };
  return p;
}
async function proposal(p:Project):Promise<BrandCampaignRollout>{
  return {
    schema:BRAND_ROLLOUT_SCHEMA,project_id:p.id,generation:p.generation,
    expected_revision:p.revision,
    brand_sha256:await brandDigest(p.production_design!.brand_profile!),
    campaign:"Source-authored original launch",scene_ids:p.scenes.slice(0,2).map(s=>s.id),
    accent:"#F5B84A",wordmark:"Mw",
    reviewer_declaration:"Source owner declaration; not independently signed",
    rationale:"Apply the exact existing allowed accent to both original semantic components",
    accepts_semantic_component_rebuild:true,
  };
}
describe("owner-reviewed source-bound multi-scene brand preflight",()=>{
  it("only previews two attached original scenes and a source-bound allowed palette",async()=>{
    const p=original(),before=structuredClone(p);
    const data=await proposal(p);
    const report=await previewBrandCampaign(p,data);
    expect(report.rows).toHaveLength(2);
    expect(report.changed_count).toBe(2);
    expect(report.rows.map(row=>row.from_accent)).toEqual(["#A5C8DF","#A5C8DF"]);
    expect(report.rows.map(row=>row.to_accent)).toEqual(["#F5B84A","#F5B84A"]);
    expect(report.committed).toBe(false);
    expect(report.output_rendered).toBe(false);
    expect(report.publication_approved).toBe(false);
    expect(validateBrandCampaignReceipt(p,data,report)).toBe(true);
    expect(p).toEqual(before);
  });
  it.each(["project_id","generation","expected_revision","brand_sha256"])(
    "does not accept stale or foreign %s",async key=>{
      const p=original(),data=await proposal(p);
      const bad={...data,[key]:key==="expected_revision"?p.revision+1:"different"} as BrandCampaignRollout;
      await expect(previewBrandCampaign(p,bad)).rejects.toThrow(/current|source-bound/i);
    }
  );
  it("refuses not-allowed color, non-policy wordmark and source duplicate scenes",async()=>{
    const p=original(),data=await proposal(p);
    await expect(previewBrandCampaign(p,{...data,accent:"#000000"})).rejects.toThrow(/allowed accent/i);
    await expect(previewBrandCampaign(p,{...data,wordmark:"Other"})).rejects.toThrow(/wordmark/i);
    await expect(previewBrandCampaign(p,{...data,scene_ids:[data.scene_ids[0],data.scene_ids[0]]}))
      .rejects.toThrow(/unique/i);
    await expect(previewBrandCampaign(p,{...data,scene_ids:[crypto.randomUUID()]})).rejects.toThrow(/attached/i);
  });
  it("blocks protected project/scene reviews, allowed policy exceptions, and ownerless changes",async()=>{
    const p=original(),data=await proposal(p);
    p.scenes[0].status="approved";
    await expect(previewBrandCampaign(p,data)).rejects.toThrow(/protected/i);
    p.scenes[0].status="draft";
    p.locks.push({id:crypto.randomUUID(),resource:"scene:"+p.scenes[1].id,kind:"style",note:"human style"});
    await expect(previewBrandCampaign(p,data)).rejects.toThrow(/lock/i);
    p.locks=[];
    p.production_design!.brand_exceptions=[{
      id:crypto.randomUUID(),rule_id:p.production_design!.brand_profile!.rules[0].id,
      scene_id:p.scenes[1].id,brand_sha256:data.brand_sha256,
      campaign:"Source launch",author:"Owner",rationale:"Local exemption",
    }];
    await expect(previewBrandCampaign(p,data)).rejects.toThrow(/exception/i);
    p.production_design!.brand_exceptions=[];
    await expect(previewBrandCampaign(p,{...data,reviewer_declaration:""})).rejects.toThrow(/owner-reviewed/i);
  });
  it("cannot label no-op or a renderer execution as a campaign rollout",async()=>{
    const p=original(),data=await proposal(p);
    p.production_design!.heroes.forEach(hero=>{hero.config.accent="#F5B84A"});
    await expect(previewBrandCampaign(p,data)).rejects.toThrow(/No existing/i);
    p.production_design!.heroes.forEach(hero=>{hero.config.accent="#A5C8DF"});
    const result=await previewBrandCampaign(p,data);
    // Deliberately decode forged wire data without widening the product's literal-false authority flags.
    const forgedPublish={...result,publication_approved:true} as unknown as typeof result;
    const forgedCommit={...result,committed:true} as unknown as typeof result;
    expect(validateBrandCampaignReceipt(p,data,forgedPublish)).toBe(false);
    expect(validateBrandCampaignReceipt(p,data,forgedCommit)).toBe(false);
  });
});
