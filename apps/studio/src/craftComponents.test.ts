import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { defaultHeroConfig, emptyProductionDesign, heroNodeId, realizeProductHero } from "./creativeProduction";
import type { HeroConfig } from "./creativeProduction";
import { fixtureProject } from "./fixture";
import { rationalSeconds } from "./types";

const id="00000000-0000-4000-8000-000000000005";
const family=(layout:"contrast_comparison"|"editorial_quote"):HeroConfig=>({
  ...defaultHeroConfig(),layout,eyebrow:"EDITORIAL / ORIGINAL STUDY",
  headline:"A deliberate original idea",
  body:"An independently authored supporting idea with clear intent",
  wordmark:"MW",foreground:"#F2F4F3",accent:"#86C8D1",motion:true,
});
function seeded(){
  const p=structuredClone(fixtureProject);
  p.schema_version=2;p.locks=[];p.reviews=[];p.branch_workspaces=[];
  p.production_design=emptyProductionDesign();
  p.scenes=[{...p.scenes[0],renderer:"motion-canvas" as const,
    nodes:[],beats:[],duration:rationalSeconds(6),start:rationalSeconds(0),
    status:"draft" as const}];
  return p;
}
describe("original editable editorial craft families",()=>{
  it("reflows two different semantic layouts across 16:9, 9:16 and square with exact stable IDs",async()=>{
    const expected=await realizeProductHero(id,defaultHeroConfig());
    for(const layout of ["contrast_comparison","editorial_quote"] as const){
      const c=family(layout);
      for(const [w,h] of [[1920,1080],[1080,1920],[1080,1080]]){
        const nodes=await realizeProductHero(id,c,w,h);
        expect(nodes).toHaveLength(6);
        expect(nodes.map(n=>n.id)).toEqual(expected.map(n=>n.id));
        expect(nodes).toEqual(await realizeProductHero(id,c,w,h));
        for(const node of nodes){
          expect(node.x).toBeGreaterThanOrEqual(w*.05);
          expect(node.y).toBeGreaterThanOrEqual(h*.05);
          expect(node.x+node.width).toBeLessThanOrEqual(w*.96);
          expect(node.y+node.height).toBeLessThanOrEqual(h*.96);
          expect(node.keyframes.length).toBeGreaterThanOrEqual(4);
          expect(node.keyframes.length).toBeLessThanOrEqual(6);
        }
        const rule=nodes.find(n=>n.name.endsWith("/ rule"))!;
        expect(rule.kind).toBe("rectangle");
        if(layout==="contrast_comparison"){
          expect(rule.height>rule.width).toBe(w>h);
          expect(nodes.at(-1)?.text).toBe("CONCEPTUAL COMPARISON / NOT MEASURED EVIDENCE");
          if(w>h)expect(nodes[2].x).toBeGreaterThan(w*.5);
          else expect(nodes[2].y).toBeGreaterThan(nodes[1].y+nodes[1].height);
        }else{
          expect(rule.width).toBeGreaterThan(rule.height);
          expect(nodes.at(-1)?.text).toBe("ILLUSTRATIVE QUOTE / ATTRIBUTION NOT VERIFIED");
        }
      }
    }
  });
  it("preserves human edits to independent fields and retains a stable original camera",async()=>{
    let p=seeded();
    const scene_id=p.scenes[0].id,config=family("contrast_comparison");
    p=await applyChange(p,{type:"upsert_product_hero",instance_id:id,scene_id,config});
    const camera=structuredClone(p.scenes[0].camera);
    const eyebrow=await heroNodeId(id,"eyebrow");
    const body=await heroNodeId(id,"body");
    const original=p.scenes[0].nodes.find(n=>n.id===body)!;
    const style={...original.style,fill:"#FFE0A0"};
    p=await applyChange(p,{type:"update_canvas_style",scene_id,node_id:body,style});
    p=await applyChange(p,{type:"update_canvas_text",scene_id,node_id:eyebrow,
      text:"HUMAN EDITED ORIGINAL"});
    p=await applyChange(p,{type:"upsert_product_hero",instance_id:id,scene_id,
      config:{...family("editorial_quote"),headline:"An authored illustrative quotation",
        body:"An original context statement"}});
    expect(p.scenes[0].camera).toEqual(camera);
    expect(p.scenes[0].nodes).toHaveLength(6);
    expect(p.scenes[0].nodes.find(n=>n.id===body)?.style.fill).toBe(style.fill);
    expect(p.scenes[0].nodes.find(n=>n.id===body)?.style.font_size).not.toBe(original.style.font_size);
    expect(p.scenes[0].nodes.find(n=>n.id===eyebrow)?.text).toBe("HUMAN EDITED ORIGINAL");
    expect(p.production_design?.heroes[0].config.layout).toBe("editorial_quote");
    expect(JSON.parse(JSON.stringify(p)).production_design?.heroes[0].id).toBe(id);
  });
  it("rejects conflicting human claims and unrenderable original copy without altering source",async()=>{
    let p=seeded();
    const scene_id=p.scenes[0].id;
    p=await applyChange(p,{type:"upsert_product_hero",instance_id:id,scene_id,
      config:family("contrast_comparison")});
    const headline=await heroNodeId(id,"headline");
    p=await applyChange(p,{type:"update_canvas_text",scene_id,node_id:headline,
      text:"Human-authored alternative"});
    const preserved=structuredClone(p);
    await expect(applyChange(p,{type:"upsert_product_hero",instance_id:id,scene_id,
      config:{...family("contrast_comparison"),headline:"Conflicting new authored copy"}}))
      .rejects.toThrow(/override conflict/);
    expect(p).toEqual(preserved);
    await expect(realizeProductHero(id,{
      ...family("editorial_quote"),headline:"UNBREAKABLEWORD".repeat(4),
    },1080,1920)).rejects.toThrow();
  });
  it("protects the safety disclosure while a conceptual component is attached",async()=>{
    for(const layout of ["contrast_comparison","editorial_quote"] as const){
      let p=seeded();
      const scene_id=p.scenes[0].id;
      p=await applyChange(p,{type:"upsert_product_hero",instance_id:id,scene_id,
        config:family(layout)});
      const safety=await heroNodeId(id,"disclosure");
      const before=structuredClone(p);
      await expect(applyChange(p,{type:"update_canvas_text",scene_id,node_id:safety,
        text:"VERIFIED PRODUCT ENDORSEMENT"})).rejects.toThrow(/disclosure/);
      const visible=p.scenes[0].nodes.find(n=>n.id===safety)!;
      await expect(applyChange(p,{type:"transform_canvas_node",scene_id,node_id:safety,
        transform:{x:visible.x,y:visible.y,width:visible.width,height:visible.height,
          rotation_deg:visible.rotation_deg,opacity:0}})).rejects.toThrow(/disclosure/);
      await expect(applyChange(p,{type:"remove_canvas_node",scene_id,node_id:safety}))
        .rejects.toThrow(/disclosure/);
      expect(p).toEqual(before);
      const detached=await applyChange(p,{type:"detach_product_hero",instance_id:id});
      const changed=await applyChange(detached,{type:"update_canvas_text",scene_id,node_id:safety,
        text:"Detached original editorial disclaimer"});
      expect(changed.production_design?.heroes).toHaveLength(0);
    }
  });
  it("renders static counterparts without fabricated native animation",async()=>{
    for(const layout of ["contrast_comparison","editorial_quote"] as const){
      const nodes=await realizeProductHero(id,{...family(layout),motion:false});
      expect(nodes.every(n=>n.keyframes.length===0)).toBe(true);
    }
  });
});
