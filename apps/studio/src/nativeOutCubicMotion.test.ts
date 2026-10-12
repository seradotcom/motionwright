import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { easedProgress, previewTransform } from "./canvasMotion";
import { fixtureProject } from "./fixture";
import type { Change, Project } from "./types";

function project(): Project {
  const p=structuredClone(fixtureProject);
  p.scenes[0].beats=[];
  p.scenes[0].nodes[0].keyframes=[];
  p.locks=[];
  p.deliverables[0].timing_locked=false;
  return p;
}
type Cubic=Extract<Change,{type:"set_canvas_out_cubic_position_motion"}>;
type Linear=Extract<Change,{type:"set_canvas_linear_position_motion"}>;
function cubic(p:Project):Cubic {
  const node=p.scenes[0].nodes[0];
  return {
    type:"set_canvas_out_cubic_position_motion",
    scene_id:p.scenes[0].id,node_id:node.id,
    deliverable_id:p.deliverables[0].id,
    start_x:node.x-80,start_y:node.y+25,end_frame:30,
  };
}
function linear(p:Project):Linear {
  const {type:_,...args}=cubic(p);
  return {type:"set_canvas_linear_position_motion",...args};
}

describe("MW05-E01-01 exact native cubic-out source motion",()=>{
  it("matches the pinned Semwright cubic-out progression without approximation",()=>{
    for(const [t,expected] of [[0,0],[.25,.578125],[.5,.875],[.75,.984375],[1,1]]) {
      expect(easedProgress(t,"ease_out_cubic")).toBeCloseTo(expected,12);
    }
  });
  it("never silently maps the studio smoothstep to cubic-out",()=>{
    expect(easedProgress(.25,"ease_in_out")).toBeCloseTo(.15625,12);
    expect(easedProgress(.25,"ease_in_out"))
      .not.toBeCloseTo(easedProgress(.25,"ease_out_cubic"),5);
  });
  it("commits four paired X/Y keys in one revision without changing the base pose",async()=>{
    const p=project(),prior=structuredClone(p),next=await applyChange(p,cubic(p));
    const node=next.scenes[0].nodes[0];
    expect(next.revision).toBe(prior.revision+1);
    expect(node.id).toBe(prior.scenes[0].nodes[0].id);
    expect([node.x,node.y]).toEqual([prior.scenes[0].nodes[0].x,prior.scenes[0].nodes[0].y]);
    expect(node.keyframes).toHaveLength(4);
    expect(node.keyframes.map(k=>k.property)).toEqual(["x","y","x","y"]);
    expect(node.keyframes.map(k=>k.interpolation))
      .toEqual(["linear","linear","ease_out_cubic","ease_out_cubic"]);
    expect(node.keyframes[2].at).toEqual({num:"1",den:"1"});
    expect(p).toEqual(prior);
  });
  it("retains exact fractional frames for 24000/1001 FPS",async()=>{
    const p=project();
    p.deliverables[0].frame_rate={num:"24000",den:"1001"};
    const next=await applyChange(p,cubic(p));
    expect(next.scenes[0].nodes[0].keyframes[2].at)
      .toEqual({num:"1001",den:"800"});
  });
  it("uses the same cubic-out preview while keeping authored node editable",async()=>{
    const p=project(),original=p.scenes[0].nodes[0];
    const next=await applyChange(p,cubic(p));
    const tween=previewTransform(next.scenes[0].nodes[0],.5);
    expect(tween.x).toBeCloseTo(original.x-10,9);
    expect(tween.y).toBeCloseTo(original.y+3.125,9);
    expect(next.scenes[0].nodes[0].style).toEqual(original.style);
    expect(next.scenes[0].nodes[0].parent_id).toBe(original.parent_id);
  });
  it("keeps the legacy linear endpoint contract intact",async()=>{
    const p=project();
    const linearNode=(await applyChange(p,linear(p))).scenes[0].nodes[0];
    expect(linearNode.keyframes.map(k=>k.interpolation)).toEqual(Array(4).fill("linear"));
    expect(previewTransform(linearNode,.5).x).toBeCloseTo(linearNode.x-40,9);
  });
  it("blocks authored scene beats without starting a new motion scope",async()=>{
    const p=project();p.scenes[0].beats=structuredClone(fixtureProject.scenes[0].beats);
    const original=structuredClone(p);
    await expect(applyChange(p,cubic(p))).rejects.toThrow("authored beats");
    expect(p).toEqual(original);
  });
  it("respects project scene-position locks",async()=>{
    const p=project();
    p.locks.push({id:crypto.randomUUID(),resource:"scene:"+p.scenes[0].id,
      kind:"position",note:"Protected original motion"});
    await expect(applyChange(p,cubic(p))).rejects.toThrow("locked");
    expect(p.scenes[0].nodes[0].keyframes).toEqual([]);
  });
  it("respects object-position locks",async()=>{
    const p=project();p.scenes[0].nodes[0].property_locks.push("position");
    await expect(applyChange(p,cubic(p))).rejects.toThrow("locked");
    expect(p.scenes[0].nodes[0].keyframes).toEqual([]);
  });
  it("refuses overwriting existing authored keys",async()=>{
    const p=project(),next=await applyChange(p,cubic(p));
    await expect(applyChange(next,cubic(next))).rejects.toThrow("overwrites");
    expect(next.scenes[0].nodes[0].keyframes).toHaveLength(4);
  });
  it("rejects noninteger, negative and unbounded output frames",async()=>{
    const p=project();
    for(const frame of [0,-1,1.2,100_000]) {
      await expect(applyChange(p,{...cubic(p),end_frame:frame})).rejects.toThrow();
    }
  });
  it("preserves node identity and original editable semantic copy",async()=>{
    const p=project(),before=structuredClone(p.scenes[0].nodes[0]);
    const after=(await applyChange(p,cubic(p))).scenes[0].nodes[0];
    expect(after.name).toBe(before.name);
    expect(after.text).toBe(before.text);
    expect(after.style).toEqual(before.style);
    expect(after.relations).toEqual(before.relations);
    expect(after.rotation_deg).toBe(before.rotation_deg);
  });
});
