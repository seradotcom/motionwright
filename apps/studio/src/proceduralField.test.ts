import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { defaultProceduralConfig, proceduralNodeId, realizeProceduralField } from "./proceduralField";
import type { Project } from "./types";
import { rationalSeconds } from "./types";

const ID="00000000-0000-4000-8000-000000000095";
function project():Project {
  const p=structuredClone(fixtureProject);
  p.branch_workspaces=[];p.locks=[];p.production_design=emptyProductionDesign();
  p.reviews=[];p.markers=[];
  p.scenes=[{...p.scenes[0],nodes:[],beats:[],start:rationalSeconds(0),
    duration:rationalSeconds(6),renderer:"motion-canvas",status:"draft"}];
  return p;
}
const upsert=(p:Project, config=defaultProceduralConfig())=>applyChange(p,{
  type:"upsert_procedural_field",instance_id:ID,scene_id:p.scenes[0].id,config,
});
describe("procedural fields / deterministic Canvas generation",()=>{
  it("is byte-identity stable and matches Rust's 12-node golden fixture",async()=>{
    const nodes=await realizeProceduralField(ID,defaultProceduralConfig());
    expect(nodes.map(n=>[n.x,n.y])).toEqual([
      [200,211],[440,302],[792,193],[993,189],
      [343,474],[594,371],[798,462],[1023,420],
      [349,642],[449,678],[652,650],[862,660],
    ]);
    expect(nodes[0].id).toBe("1b1d1609-6443-84ef-ae30-15b66241f589");
    expect(nodes[1].id).toBe("47e4e8df-db4c-81f2-8118-f1e2da44e052");
    expect(nodes[11].id).toBe("3bea5c37-80c3-819f-8bf9-b6d8a54f88c2");
    expect(nodes[0].id).toBe(await proceduralNodeId(ID,0));
    expect(nodes.every(n=>n.kind==="rectangle"&&n.style.fill==="#A5C8DF"
      &&n.opacity===.85 &&n.keyframes.length===0)).toBe(true);
    const grid=await realizeProceduralField(ID,{...defaultProceduralConfig(),distribution:"grid"});
    expect(grid.map(n=>n.id)).toEqual(nodes.map(n=>n.id));
    expect(grid[0].x).not.toBe(nodes[0].x);
  });
  it("rejects impossible and unaffordable requests before generating",async()=>{
    const valid=defaultProceduralConfig();
    for(const bad of [{...valid,count:65},{...valid,count:0},{...valid,columns:0},
      {...valid,size:129},{...valid,opacity_percent:0},
      {...valid,area_width:1800},{...valid,count:64,area_height:40},
      {...valid,seed:-1},{...valid,seed:2**32}])
      await expect(realizeProceduralField(ID,bad)).rejects.toThrow();
    expect((await realizeProceduralField(ID,valid)).length).toBe(12);
  });
  it("reflows quantity and seed without destroying human style edits",async()=>{
    let p=await upsert(project());
    const first=p.scenes[0].nodes.map(n=>n.id);
    const edited=await proceduralNodeId(ID,1);
    const original=p.scenes[0].nodes.find(n=>n.id===edited)!;
    p=await applyChange(p,{type:"update_canvas_style",scene_id:p.scenes[0].id,
      node_id:edited,style:{...original.style,fill:"#FFAA22"}});
    const next={...defaultProceduralConfig(),seed:123,count:18};
    p=await upsert(p,next);
    expect(p.scenes[0].nodes).toHaveLength(18);
    expect(p.scenes[0].nodes.slice(0,12).map(n=>n.id)).toEqual(first);
    expect(p.scenes[0].nodes.find(n=>n.id===edited)?.style.fill).toBe("#FFAA22");
    expect(p.production_design?.procedural_fields?.[0].baseline[1].style.fill).toBe("#A5C8DF");
    expect(JSON.parse(JSON.stringify(p)).production_design.procedural_fields).toHaveLength(1);
  });
  it("declines to shrink over a human edited suffix",async()=>{
    let p=await upsert(project());
    const last=await proceduralNodeId(ID,11);
    const n=p.scenes[0].nodes.find(x=>x.id===last)!;
    p=await applyChange(p,{type:"update_canvas_style",scene_id:p.scenes[0].id,
      node_id:last,style:{...n.style,fill:"#AA1111"}});
    const before=structuredClone(p);
    await expect(upsert(p,{...defaultProceduralConfig(),count:8})).rejects.toThrow("human edits");
    expect(p).toEqual(before);
    p=await applyChange(p,{type:"detach_procedural_field",instance_id:ID});
    expect(p.production_design?.procedural_fields).toHaveLength(0);
    expect(p.scenes[0].nodes).toHaveLength(12);
  });
  it("rejects tampered baselines before any three-way merge",async()=>{
    const p=await upsert(project());
    p.production_design!.procedural_fields![0].baseline[0].x+=100;
    await expect(upsert(p,{...defaultProceduralConfig(),seed:43})).rejects.toThrow("baseline mismatch");
  });
  it("allows unmodified suffix reduction with stable prefix IDs and new reflow",async()=>{
    let p=await upsert(project());const ids=p.scenes[0].nodes.map(n=>n.id), oldY=p.scenes[0].nodes[0].y;
    p=await upsert(p,{...defaultProceduralConfig(),count:8});
    expect(p.scenes[0].nodes).toHaveLength(8);
    expect(p.scenes[0].nodes.map(n=>n.id)).toEqual(ids.slice(0,8));
    expect(p.scenes[0].nodes[0].y).not.toBe(oldY);
  });
});
describe("procedural 30fps entrance sequencing",()=>{
  it("generates canonical per-node frame keys equivalent to Rust",async()=>{
    const config={...defaultProceduralConfig(),count:5,
      reveal_step_frames:3,reveal_duration_frames:12};
    const nodes=await realizeProceduralField(ID,config);
    expect(nodes[0].keyframes.map(k=>k.at)).toEqual([{num:"0",den:"1"},{num:"2",den:"5"}]);
    expect(nodes[1].keyframes.map(k=>k.at)).toEqual([
      {num:"0",den:"1"},{num:"1",den:"10"},{num:"1",den:"2"},
    ]);
    expect(nodes[1].keyframes.at(-1)?.interpolation).toBe("linear");
    expect(nodes[4].keyframes.at(-1)?.value).toBe(.85);
    let p=await upsert(project(),config);
    expect(p.scenes[0].nodes[1].keyframes).toEqual(nodes[1].keyframes);
    const prior=structuredClone(p);
    const invalid={...defaultProceduralConfig(),count:30,
      reveal_step_frames:10,reveal_duration_frames:60};
    await expect(upsert(p,invalid)).rejects.toThrow("sequence exceeds scene duration");
    expect(p).toEqual(prior);
  });
  it("keeps a previous unanimated field when sequencing fields are absent",async()=>{
    const original=defaultProceduralConfig();
    const old=structuredClone(original) as Partial<typeof original>;
    delete old.reveal_step_frames;delete old.reveal_duration_frames;
    const nodes=await realizeProceduralField(ID,old as typeof original);
    expect(nodes).toEqual(await realizeProceduralField(ID,original));
    expect(nodes.every(n=>n.keyframes.length===0)).toBe(true);
  });
});
