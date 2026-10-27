import {describe,expect,it} from "vitest";
import {applyChange} from "./api";
import { brandDigest } from "./brandGovernance";
import {emptyProductionDesign} from "./creativeProduction";
import {fixtureProject} from "./fixture";
import {rationalSeconds} from "./types";
import {
  defaultProductStageConfig, realizeProductStage, validateProductStageConfig,
  PRODUCT_STAGE_DISCLOSURE,stageNodeId,STAGE_ROLES,
} from "./productStage";
import type {ProductStageConfig} from "./productStage";
import type {Project} from "./types";

const id="00000000-0000-4000-8000-000000000005";
const ready=():ProductStageConfig=>({
  ...defaultProductStageConfig(),
  author_attestation:"I authored this original UI graphic and authorize its publication.",
});
function seed():Project{
  const p=structuredClone(fixtureProject);
  p.schema_version=2;
  p.production_design=emptyProductionDesign();
  p.locks=[];p.branch_workspaces=[];p.reviews=[];
  p.scenes=p.scenes.slice(0,1).map(scene=>({
    ...scene,renderer:"motion-canvas" as const,status:"draft" as const,
    nodes:[],beats:[],duration:rationalSeconds(6),start:rationalSeconds(0),
  }));
  return p;
}
const commit=(project:Project,config:ProductStageConfig)=>applyChange(project,{
  type:"upsert_product_stage",instance_id:id,scene_id:project.scenes[0].id,config,
});
describe("ProductStage generic original UI grammar",()=>{
  it("reflows browser, panel and handset over three aspects without moving normalized focus",async()=>{
    const ids=await Promise.all(STAGE_ROLES.map(role=>stageNodeId(id,role)));
    for(const frame of ["browser","flat_panel","generic_handset"] as const){
      for(const [w,h] of [[1920,1080],[1080,1920],[1080,1080]]){
        const nodes=await realizeProductStage(id,{...ready(),frame},w,h);
        expect(nodes.map(n=>n.id)).toEqual(ids);
        expect(nodes).toHaveLength(14);
        expect(nodes.every(n=>n.x>=w*.04 && n.y>=h*.04 &&
          n.x+n.width<=w*.96 && n.y+n.height<=h*.95)).toBe(true);
        const screen=nodes[1],focus=nodes[10];
        expect(focus.x+focus.width/2).toBeCloseTo(screen.x+screen.width*.510,8);
        expect(focus.y+focus.height/2).toBeCloseTo(screen.y+screen.height*.620,8);
        expect(nodes[13].text).toBe(PRODUCT_STAGE_DISCLOSURE);
      }
    }
  });
  it("changing screen content preserves screen, shell, focus, camera and compatible human style",async()=>{
    let p=await commit(seed(),ready());
    const frame=structuredClone(p.scenes[0].nodes[0]);
    const screen=structuredClone(p.scenes[0].nodes[1]);
    const focus=structuredClone(p.scenes[0].nodes[10]);
    const camera=structuredClone(p.scenes[0].camera);
    const nodeId=await stageNodeId(id,"screen_detail");
    const before=p.scenes[0].nodes.find(n=>n.id===nodeId)!;
    const newStyle={...before.style,fill:"#FFE2A0"};
    p=await applyChange(p,{type:"update_canvas_style",scene_id:p.scenes[0].id,node_id:nodeId,
      style:newStyle});
    const updated={...ready(),screen_heading:"The new original benefit",
      screen_detail:"Another authored clear workflow",action_label:"Continue"};
    p=await commit(p,updated);
    expect(p.scenes[0].nodes.slice(0,2)).toEqual([frame,screen]);
    expect(p.scenes[0].nodes.find(n=>n.id===focus.id)).toEqual(focus);
    expect(p.scenes[0].camera).toEqual(camera);
    expect(p.scenes[0].nodes.find(n=>n.id===nodeId)?.style).toEqual(newStyle);
    expect(p.scenes[0].nodes.find(n=>n.id===nodeId)?.text).toBe(updated.screen_detail);
    expect(p.production_design?.stages?.[0].baseline).toHaveLength(14);
  });
  it("keeps identical native editable entrance keyframes when only text is replaced",async()=>{
    const config={...ready(),animate_focus:true};
    let p=await commit(seed(),config);
    const focus=p.scenes[0].nodes[10];
    expect(focus.keyframes).toHaveLength(4);
    expect(focus.keyframes[1].at).toEqual({num:"3",den:"10"});
    p=await commit(p,{...config,screen_heading:"A better original UI study"});
    expect(p.scenes[0].nodes[10]).toEqual(focus);
  });
  it("rejects untyped focus flags before realization or browser-demo mutation",async()=>{
    const project=seed(),before=structuredClone(project);
    for(const animate_focus of ["false",0,null]){
      const config={...ready(),animate_focus} as unknown as ProductStageConfig;
      expect(()=>validateProductStageConfig(config)).toThrow(/focus animation.*boolean/);
      await expect(realizeProductStage(id,config)).rejects.toThrow(/focus animation.*boolean/);
      await expect(commit(project,config)).rejects.toThrow(/focus animation.*boolean/);
      expect(project).toEqual(before);
      expect(config.animate_focus).toBe(animate_focus);
    }
  });
  it("requires original-content declaration; rejects external media, logos and deletion of disclosure",async()=>{
    const p=seed();
    await expect(commit(p,defaultProductStageConfig())).rejects.toThrow(/attestation/);
    const illicit={...ready(),external_image_asset:"unlicensed-company-logo.png"} as unknown as ProductStageConfig;
    expect(()=>validateProductStageConfig(illicit)).toThrow(/not admitted/);
    await expect(commit(p,illicit)).rejects.toThrow(/not admitted/);
    await expect(commit(p,{...ready(),frame:"iphone" as "browser"})).rejects.toThrow(/generic/);
    const stage=await commit(p,ready());
    const disclosure=await stageNodeId(id,"disclosure");
    await expect(applyChange(stage,{type:"update_canvas_text",scene_id:stage.scenes[0].id,
      node_id:disclosure,text:"VERIFIED REAL CAPTURE"})).rejects.toThrow(/disclosure/);
  });
  it("rejects conflicting human edits and retains all source objects after detach",async()=>{
    let p=await commit(seed(),ready());
    const sceneId=p.scenes[0].id;
    const heading=await stageNodeId(id,"screen_heading");
    p=await applyChange(p,{type:"update_canvas_text",scene_id:sceneId,node_id:heading,
      text:"Human protected headline"});
    await expect(commit(p,{...ready(),screen_heading:"A regenerating headline"})).rejects.toThrow(/conflict/);
    const before=structuredClone(p.scenes[0].nodes);
    p=await applyChange(p,{type:"detach_product_stage",instance_id:id});
    expect(p.scenes[0].nodes).toEqual(before);
    expect(p.production_design?.stages).toHaveLength(0);
    p=await applyChange(p,{type:"update_canvas_text",scene_id:sceneId,
      node_id:await stageNodeId(id,"disclosure"),text:"Detached copy"});
    expect(p.scenes[0].nodes.at(-1)?.text).toBe("Detached copy");
  });
  it("accepts actual native fixture copy in the bounded browser and handset anchor regions",async()=>{
    for(const frame of ["browser","generic_handset"] as const){
      const config={...ready(),frame,screen_heading:"Clear original workflow",
        screen_detail:"Editable original interface",action_label:"Continue",animate_focus:true};
      const nodes=await realizeProductStage(id,config,1920,1080);
      expect(nodes).toHaveLength(14);
      expect(nodes.filter(n=>n.kind==="text")).toHaveLength(6);
    }
  });
  it("refuses overlong native text that cannot fit a handset screen rather than clipping it",async()=>{
    const config={...ready(),frame:"generic_handset" as const,screen_heading:"M".repeat(40)};
    await expect(realizeProductStage(id,config,1920,1080)).rejects.toThrow(/screen anchor/);
  });
});

describe("brand-governed generic ProductStage",()=>{
  it("enforces BrandProfile palette on configured and human-edited focus and supports exact scoped waiver",async()=>{
    let p=await commit(seed(),ready());
    const rule=crypto.randomUUID(),sceneId=p.scenes[0].id;
    const brand={id:crypto.randomUUID(),label:"Owned UI accent palette",version:1,
      rules:[{kind:"allowed_accents" as const,id:rule,colors:["#86C8D1"]}]};
    p=await applyChange(p,{type:"set_brand_governance",profile:brand,exceptions:[]});
    const forbidden={...ready(),accent:"#F06020"};
    await expect(commit(p,forbidden)).rejects.toThrow(/Brand rule/);
    const focus=await stageNodeId(id,"focus_marker");
    const original=p.scenes[0].nodes.find(n=>n.id===focus)!;
    expect(original.style.fill).toBe("#86C8D1");
    await expect(applyChange(p,{type:"update_canvas_style",scene_id:sceneId,
      node_id:focus,style:{...original.style,fill:"#F06020"}})).rejects.toThrow(/Brand rule/);
    const digest=await brandDigest(brand);
    p=await applyChange(p,{type:"set_brand_governance",profile:brand,exceptions:[{
      id:crypto.randomUUID(),rule_id:rule,scene_id:sceneId,brand_sha256:digest,
      campaign:"One reviewed original-UI campaign",author:"Declaring owner",
      rationale:"Exact-scene alternate source palette approval",
    }]});
    p=await commit(p,forbidden);
    expect(p.production_design?.stages?.[0].config.accent).toBe("#F06020");
  });
  it("never silently bypasses a required wordmark on a generic stage",async()=>{
    const rule=crypto.randomUUID();
    const brand={id:crypto.randomUUID(),label:"Required wordmark policy",version:1,
      rules:[{kind:"required_wordmark" as const,id:rule,text:"OWNER"}]};
    let p=await applyChange(seed(),{type:"set_brand_governance",profile:brand,exceptions:[]});
    await expect(commit(p,ready())).rejects.toThrow(/Brand rule/);
    const digest=await brandDigest(brand);
    p=await applyChange(p,{type:"set_brand_governance",profile:brand,exceptions:[{
      id:crypto.randomUUID(),rule_id:rule,scene_id:p.scenes[0].id,
      brand_sha256:digest,campaign:"One scoped UI study",author:"Approving owner",
      rationale:"No real-product-capture claim permitted",
    }]});
    p=await commit(p,ready());
    expect(p.production_design?.stages).toHaveLength(1);
  });
});
