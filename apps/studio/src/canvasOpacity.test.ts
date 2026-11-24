import {describe, expect, it} from "vitest";
import {fixtureProject} from "./fixture";
import {exactCanvasOpacitySpans} from "./canvasOpacity";
import {previewTransform} from "./canvasMotion";
import type {CanvasKeyframe} from "./types";

function source() {
  const node = structuredClone(fixtureProject.scenes[0].nodes[0]);
  node.opacity = 1;
  const key = (num:string, den:string, value:number, interpolation:CanvasKeyframe["interpolation"]):CanvasKeyframe =>
    ({at:{num,den},property:"opacity",value,interpolation});
  node.keyframes = [key("0","1",0,"linear"),key("1001","3000",1,"ease_out_cubic"),
    key("1001","1500",1,"hold"),key("1001","1000",0,"linear")];
  return {node,scene:{duration:{num:"2",den:"1"}},fps:{num:"30000",den:"1001"}};
}
describe("exact Native visibility intervals", () => {
  it("keeps fractional frame boundaries and a real hold without emitting an invented transition", () => {
    const {node,scene,fps} = source(), original = structuredClone(node);
    const spans = exactCanvasOpacitySpans(node,scene,fps)!;
    expect(spans).toHaveLength(2);
    expect(spans[0]).toMatchObject({start:{num:"0",den:"1"},end:{num:"1001",den:"3000"},from:0,to:1,curve:"ease_out_cubic"});
    expect(spans[1]).toMatchObject({start:{num:"1001",den:"1500"},end:{num:"1001",den:"1000"},from:1,to:0,curve:"linear"});
    expect(previewTransform(node,1001/4000).opacity).toBeCloseTo(1-(.25**3));
    expect(previewTransform(node,.5).opacity).toBe(1);
    expect(previewTransform(node,1001/1200).opacity).toBeCloseTo(.5);
    expect(node).toEqual(original);
    for (const value of [0,1]) {
      node.keyframes = node.keyframes.map(key=>({...key,value}));
      expect(exactCanvasOpacitySpans(node,scene,fps)).toEqual([]);
      expect(previewTransform(node,1.2).opacity).toBe(value);
    }
  });
  it("rejects partial alpha, held jumps, duplicates, bad curves, off-frame and excess source keys", () => {
    const edits = [
      (n:ReturnType<typeof source>["node"])=>{n.keyframes[1].value=.6;},
      (n:ReturnType<typeof source>["node"])=>{n.keyframes[1].interpolation="hold";},
      (n:ReturnType<typeof source>["node"])=>{n.keyframes.push(structuredClone(n.keyframes[1]));},
      (n:ReturnType<typeof source>["node"])=>{n.keyframes[1].interpolation="ease_in_out";},
      (n:ReturnType<typeof source>["node"])=>{n.keyframes[1].at={num:"1",den:"7"};},
      (n:ReturnType<typeof source>["node"])=>{n.keyframes[3].at={num:"2",den:"1"};},
      (n:ReturnType<typeof source>["node"])=>{n.keyframes.push(...Array(5).fill(n.keyframes[0]));},
    ];
    for (const edit of edits) {
      const {node,scene,fps}=source();edit(node);
      const before=structuredClone(node);
      expect(()=>exactCanvasOpacitySpans(node,scene,fps)).toThrow(/frame-aligned/);
      expect(node).toEqual(before);
    }
  });
});
