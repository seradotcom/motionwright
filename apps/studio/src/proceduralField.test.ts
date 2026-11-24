import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { brandDigest } from "./brandGovernance";
import { defaultProceduralConfig, proceduralNodeId, realizeProceduralField, validateProceduralAuthoringBounds } from "./proceduralField";
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
      &&n.opacity===1 &&n.keyframes.length===0)).toBe(true);
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
    expect(nodes[0].keyframes.filter(k=>k.property==="opacity").map(k=>k.at)).toEqual([{num:"0",den:"1"},{num:"2",den:"5"}]);
    expect(nodes[1].keyframes.filter(k=>k.property==="opacity").map(k=>k.at)).toEqual([
      {num:"0",den:"1"},{num:"1",den:"10"},{num:"1",den:"2"},
    ]);
    expect(nodes[1].keyframes.filter(k=>k.property==="y").map(k=>k.at)).toEqual([
      {num:"0",den:"1"},{num:"1",den:"10"},{num:"1",den:"2"},
    ]);
    expect(nodes[1].keyframes.at(-1)?.interpolation).toBe("ease_out_cubic");
    expect(nodes[4].keyframes.at(-1)?.value).toBe(1);
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


describe("governed procedural authoring",()=>{
  it("checks field config and live overrides, preserving scene-specific waivers",async()=>{
    let p=project();
    const rule=crypto.randomUUID();
    const brand={id:crypto.randomUUID(),label:"Governed field",version:1,
      rules:[{kind:"allowed_accents" as const,id:rule,colors:["#A5C8DF"]}]};
    p=await applyChange(p,{type:"set_brand_governance",profile:brand,exceptions:[]});
    p=await upsert(p);
    const wrong={...defaultProceduralConfig(),fill:"#FF2200"};
    await expect(upsert(p,wrong)).rejects.toThrow("Brand rule");
    const node=p.scenes[0].nodes[0];
    await expect(applyChange(p,{type:"update_canvas_style",scene_id:p.scenes[0].id,
      node_id:node.id,style:{...node.style,fill:"#FF2200"}})).rejects.toThrow("Brand rule");
    const digest=await brandDigest(brand);
    p=await applyChange(p,{type:"set_brand_governance",profile:brand,exceptions:[{
      id:crypto.randomUUID(),rule_id:rule,scene_id:p.scenes[0].id,
      brand_sha256:digest,campaign:"One campaign",author:"Declared editor",
      rationale:"Bounded palette waiver on this scene only",
    }]});
    p=await upsert(p,wrong);
    expect(p.production_design?.procedural_fields?.[0].config.fill).toBe("#FF2200");
    expect(p.production_design?.brand_exceptions?.[0].scene_id).toBe(p.scenes[0].id);
  });
});


describe("procedural orbit and shape conformance",()=>{
  const goldenRadial=[
    [889,638],[698,692],[489,686],[279,601],[189,485],[199,358],
    [339,230],[530,176],[739,182],[949,267],[1039,383],[1029,510],
  ];
  const goldenSpiral=[
    [636,451],[628,477],[583,497],[503,489],[437,455],[407,396],
    [454,315],[558,262],[708,245],[893,295],[1004,387],[1029,510],
  ];
  it("matches fixed-point Rust golden positions and stable circle identities",async()=>{
    const config={...defaultProceduralConfig(),distribution:"radial" as const,shape:"circle" as const};
    const radial=await realizeProceduralField(ID,config);
    expect(radial.map(n=>[n.x,n.y])).toEqual(goldenRadial);
    expect(radial.every(n=>n.kind==="circle")).toBe(true);
    const spiral=await realizeProceduralField(ID,{...config,distribution:"spiral"});
    expect(spiral.map(n=>[n.x,n.y])).toEqual(goldenSpiral);
    expect(spiral.map(n=>n.id)).toEqual(radial.map(n=>n.id));
  });
  it("changes from square to circle without clobbering human style",async()=>{
    let p=await upsert(project());
    const selected=p.scenes[0].nodes[1];
    p=await applyChange(p,{type:"update_canvas_style",scene_id:p.scenes[0].id,
      node_id:selected.id,style:{...selected.style,fill:"#E4A621"}});
    p=await upsert(p,{...defaultProceduralConfig(),distribution:"radial",shape:"circle"});
    expect(p.scenes[0].nodes.every(n=>n.kind==="circle")).toBe(true);
    expect(p.scenes[0].nodes[1].style.fill).toBe("#E4A621");
    expect(p.scenes[0].nodes[1].id).toBe(selected.id);
  });
  it("preserves bounded scene geometry and unique IDs at all count and seed extremes",async()=>{
    for(const distribution of ["radial","spiral"] as const)
      for(const seed of [0,1,41,4294967295])
        for(const count of [1,2,3,12,31,63,64]){
          const config={...defaultProceduralConfig(),distribution,seed,count,columns:16,shape:"circle" as const};
          const nodes=await realizeProceduralField(ID,config);
          expect(nodes).toHaveLength(count);
          expect(new Set(nodes.map(n=>n.id)).size).toBe(count);
          expect(nodes.every(n=>n.x>=config.origin_x && n.y>=config.origin_y &&
            n.x+n.width<=config.origin_x+config.area_width &&
            n.y+n.height<=config.origin_y+config.area_height && n.kind==="circle")).toBe(true);
        }
  });
  it("rejects unbounded or forged orbit and decodes legacy unshaped squares",async()=>{
    for(const distribution of ["radial","spiral"] as const)
      await expect(realizeProceduralField(ID,{...defaultProceduralConfig(),
        distribution,area_width:80,size:60})).rejects.toThrow(/orbit/);
    const old=structuredClone(defaultProceduralConfig());
    delete old.shape;
    expect(await realizeProceduralField(ID,old)).toEqual(await realizeProceduralField(ID,defaultProceduralConfig()));
  });
});

describe("procedural entrance authoring bounds without v1 migration",()=>{
  it("rejects clipped new entrances in every distribution and preserves prior human edits",async()=>{
    let saved=await upsert(project());
    const first=saved.scenes[0].nodes[0];
    saved=await applyChange(saved,{type:"update_canvas_style",scene_id:saved.scenes[0].id,
      node_id:first.id,style:{...first.style,fill:"#FFAA22"}});
    for(const distribution of ["grid","staggered","scatter","radial","spiral"] as const){
      const config={...defaultProceduralConfig(),count:1,columns:1,distribution,
        origin_x:0,origin_y:1064,area_width:16,area_height:16,size:8,
        reveal_step_frames:1,reveal_duration_frames:12};
      const legacyNodes=await realizeProceduralField(ID,config);
      expect(legacyNodes.every(node=>node.y+node.height<=1080)).toBe(true);
      expect(()=>validateProceduralAuthoringBounds(legacyNodes)).toThrow("entrance exceeds");
      for(const current of [project(),saved]){
        const before=structuredClone(current);
        await expect(upsert(current,config)).rejects.toThrow("entrance exceeds");
        expect(current).toEqual(before);
      }
    }
  });
  it("accepts static edge content and the exact animated edge without shifting or retiming",async()=>{
    const base={...defaultProceduralConfig(),count:1,columns:1,distribution:"grid" as const,
      origin_x:0,origin_y:1072,area_width:8,area_height:8,size:8};
    const still=await upsert(project(),base);
    expect(still.scenes[0].nodes[0].y+still.scenes[0].nodes[0].height).toBe(1080);
    expect(still.scenes[0].nodes[0].keyframes).toEqual([]);
    const exact={...base,origin_y:1060,reveal_step_frames:1,reveal_duration_frames:12};
    const fitting=await upsert(project(),exact),node=fitting.scenes[0].nodes[0];
    expect(fitting.production_design?.procedural_fields?.[0].config).toEqual(exact);
    expect(node.y).toBe(1060);expect(node.height).toBe(8);
    expect(node.keyframes.filter(key=>key.property==="y").map(key=>[key.at,key.value])).toEqual([
      [{num:"0",den:"1"},1072],[{num:"2",den:"5"},1060],
    ]);
    const before=structuredClone(fitting);
    await expect(upsert(fitting,{...exact,origin_y:1061})).rejects.toThrow("entrance exceeds");
    expect(fitting).toEqual(before);
  });
  it("keeps a serialized legacy clipped baseline readable and detachable",async()=>{
    const legacy=await upsert(project());
    const config={...defaultProceduralConfig(),count:1,columns:1,distribution:"grid" as const,
      origin_x:0,origin_y:1072,area_width:8,area_height:8,size:8,
      reveal_step_frames:1,reveal_duration_frames:12};
    const baseline=await realizeProceduralField(ID,config);
    legacy.scenes[0].nodes=structuredClone(baseline);
    legacy.production_design!.procedural_fields=[{id:ID,scene_id:legacy.scenes[0].id,
      generator_version:1,config:structuredClone(config),baseline:structuredClone(baseline)}];
    const bytes=JSON.stringify(legacy),reopened=JSON.parse(bytes) as Project;
    expect(JSON.stringify(reopened)).toBe(bytes);
    expect(await realizeProceduralField(ID,reopened.production_design!.procedural_fields![0].config)).toEqual(baseline);
    expect(baseline[0].keyframes.find(key=>key.property==="y")?.value).toBe(1084);
    const before=structuredClone(reopened);
    await expect(upsert(reopened,config)).rejects.toThrow("entrance exceeds");
    expect(reopened).toEqual(before);
    const detached=await applyChange(reopened,{type:"detach_procedural_field",instance_id:ID});
    expect(detached.production_design?.procedural_fields).toHaveLength(0);
    expect(detached.scenes[0].nodes).toEqual(baseline);
  });
});

import { defaultProceduralInfluence } from "./proceduralField";
import type { ProceduralConfig, ProceduralInfluence } from "./proceduralField";

const influence = (patch: Partial<ProceduralInfluence> = {}): ProceduralInfluence => ({
  ...defaultProceduralInfluence(), center_x: 350, center_y: 250, radius: 200,
  offset_x: 20, offset_y: -10, scale_percent: 150, rotation_deg: 30, fill: "#FFFFFF", ...patch,
});
const influenced = (field = influence()): ProceduralConfig => ({
  ...defaultProceduralConfig(), distribution: "grid", count: 3, columns: 3,
  origin_x: 200, origin_y: 200, area_width: 300, area_height: 100, size: 40, fill: "#000000", influence: field,
});
const poses = (nodes: Awaited<ReturnType<typeof realizeProceduralField>>) =>
  nodes.map(node => [node.x, node.y, node.width, node.rotation_deg, node.style.fill]);

describe("bounded procedural influence components", () => {
  it("matches independently calculated complete pose/color fixtures and keeps original identities", async () => {
    const config = influenced(), nodes = await realizeProceduralField(ID, config);
    expect(poses(nodes)).toEqual([
      [235, 220, 50, 15, "#7F7F7F"], [340, 210, 60, 30, "#FFFFFF"], [435, 220, 50, 15, "#7F7F7F"],
    ]);
    const smooth = await realizeProceduralField(ID, influenced(influence({ center_x: 400, falloff: "smoothstep" })));
    expect(poses(smooth)).toEqual([
      [231.5, 227.5, 43, 4, "#272727"], [338, 214, 56, 25, "#D7D7D7"], [438, 214, 56, 25, "#D7D7D7"],
    ]);
    const plain = { ...config }; delete plain.influence;
    expect(nodes.map(node => node.id)).toEqual((await realizeProceduralField(ID, plain)).map(node => node.id));
    const outside = await realizeProceduralField(ID, influenced(influence({ radius: 100 })));
    expect(poses(outside)[0]).toEqual([230, 230, 40, 0, "#000000"]);
    const descending = await realizeProceduralField(ID, { ...config, fill: "#FFFFFF", influence: influence({ fill: "#000000" }) });
    expect(descending[0].style.fill).toBe("#808080");
  });
  it("retains neutral legacy realizations and sequences the influenced terminal geometry", async () => {
    const base = defaultProceduralConfig();
    expect(await realizeProceduralField(ID, { ...base, influence: defaultProceduralInfluence() })).toEqual(await realizeProceduralField(ID, base));
    for (const distribution of ["grid", "staggered", "scatter", "radial", "spiral"] as const) {
      const config = { ...base, distribution, shape: "circle" as const, reveal_step_frames: 3,
        influence: { ...defaultProceduralInfluence(), scale_percent: 125, offset_x: 10, offset_y: -5, fill: "#D0AA80" } };
      const nodes = await realizeProceduralField(ID, config);
      expect(nodes).toHaveLength(base.count); validateProceduralAuthoringBounds(nodes);
      for (const node of nodes) {
        const y = node.keyframes.filter(key => key.property === "y");
        expect(y[0].value).toBe(node.y + 12); expect(y.at(-1)?.value).toBe(node.y);
        expect(node.rotation_deg).toBe(0); expect(node.kind).toBe("circle");
      }
    }
  });
  it("updates one persisted field with human overrides intact and refuses conflicting or out-of-bounds edits atomically", async () => {
    let p = await upsert(project(), influenced());
    const first = p.scenes[0].nodes[0], style = { ...first.style, stroke: "#123456", stroke_width: 2 };
    p = await applyChange(p, { type: "update_canvas_style", scene_id: p.scenes[0].id, node_id: first.id, style });
    p = await upsert(p, influenced(influence({ offset_x: -20, fill: "#40A0FF" })));
    expect(p.scenes[0].nodes[0].style.stroke).toBe("#123456"); expect(p.scenes[0].nodes[0].style.stroke_width).toBe(2);
    const reopened = JSON.parse(JSON.stringify(p)) as Project;
    expect(reopened.production_design!.procedural_fields![0].config.influence!.offset_x).toBe(-20);
    const current = reopened.scenes[0].nodes[0];
    const custom = await applyChange(reopened, { type: "update_canvas_style", scene_id: current ? reopened.scenes[0].id : "", node_id: current.id,
      style: { ...current.style, fill: "#010203" } });
    const before = structuredClone(custom);
    await expect(upsert(custom, influenced(influence({ fill: "#F0F0F0" })))).rejects.toThrow("override conflict"); expect(custom).toEqual(before);
    await expect(upsert(custom, influenced(influence({ offset_x: 1920 })))).rejects.toThrow("stage"); expect(custom).toEqual(before);
  });
  it("rejects oversized, unsafe and unsupported coupled field parameters before publishing", async () => {
    for (const field of [
      influence({ radius: 0 }), influence({ center_y: 1081 }), influence({ scale_percent: 401 }),
      influence({ rotation_deg: 361 }), influence({ fill: "red" }),
      influence({ offset_x: 1920 }), influence({ scale_percent: 400 }),
      { ...influence(), effect_grant: "not-an-authoring-field" } as ProceduralInfluence,
    ]) await expect(realizeProceduralField(ID, influenced(field))).rejects.toThrow();
    const entering = await realizeProceduralField(ID, { ...influenced(), reveal_step_frames: 3 });
    expect(entering.map(node => node.rotation_deg)).toEqual([15, 30, 15]);
    expect(entering.every(node => node.keyframes.length >= 4 && node.keyframes.every(key => key.property !== "rotation_deg"))).toBe(true);
    const base = await upsert(project(), influenced(influence({ rotation_deg: 0 })));
    const lock = { ...fixtureProject.locks[0], id: crypto.randomUUID(), resource: "scene:" + base.scenes[0].id, kind: "timing" as const };
    const locked = { ...base, locks: [lock] }, before = structuredClone(locked);
    await expect(upsert(locked, influenced(influence({ rotation_deg: 0, offset_x: 0 })))).rejects.toThrow("locked"); expect(locked).toEqual(before);
  });
});
