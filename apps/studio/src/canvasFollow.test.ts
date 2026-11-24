import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { previewTransform } from "./canvasMotion";
import { alignmentScale } from "./canvasAlignment";
import { resolveCanvasRelations } from "./canvasFollow";
import type { CanvasNode, MotionInterpolation, RelationKind } from "./types";

const clock={duration:{num:"2",den:"1"},fps:{num:"30",den:"1"},continuous:true};
function fixture(curve: MotionInterpolation="linear"){
  const seed=structuredClone(fixtureProject.scenes[0].nodes[0]);
  const shape=(id:string,x:number,y:number):CanvasNode=>({...structuredClone(seed),id,kind:"rectangle",
    parent_id:null,coordinate_space:"project_pixels",x,y,width:160,height:100,
    opacity:1,rotation_deg:0,text:null,keyframes:[],relations:[],property_locks:[],
    style:{...seed.style,blend_mode:"normal"}});
  const a=shape("a",620,340),b=shape("b",920,480),c=shape("c",1200,590);
  const link=(node:CanvasNode,target:CanvasNode,kind:RelationKind="follow")=>
    node.relations.push({id:node.id+"-"+kind,target_id:target.id,kind});
  a.keyframes=[
    {at:{num:"0",den:"1"},property:"x",value:420,interpolation:"linear"},
    {at:{num:"0",den:"1"},property:"y",value:240,interpolation:"linear"},
    {at:{num:"1",den:"1"},property:"x",value:a.x,interpolation:curve},
    {at:{num:"1",den:"1"},property:"y",value:a.y,interpolation:curve},
  ];
  link(b,a);link(c,b);
  return {a,b,c,nodes:[c,b,a],link,shape};
}

describe("Follow preserves saved terminal offsets on exact native translation",()=>{
  it.each(["linear","ease_out_cubic"] as const)("composes a chain with %s timing, without saved keys",curve=>{
    const {a,b,c,nodes}=fixture(curve),before=structuredClone(nodes);
    for(const [w,h] of [[1920,1080],[1080,1920],[1080,1080]]){
      const result=resolveCanvasRelations(nodes,alignmentScale(w,h,"replan"),false,clock);
      expect(result.issue).toBeNull();
      expect(result.tracks.size).toBe(2);
      expect(result.tracks.has(a.id)).toBe(false);
      for(const t of [0,.2,.5,.9,1,1.75]){
        const A=previewTransform(a,t),B=previewTransform({...b,keyframes:result.tracks.get(b.id)!},t),
          C=previewTransform({...c,keyframes:result.tracks.get(c.id)!},t);
        expect(B.x-A.x).toBeCloseTo(b.x-a.x,9);expect(B.y-A.y).toBeCloseTo(b.y-a.y,9);
        expect(C.x-B.x).toBeCloseTo(c.x-b.x,9);expect(C.y-B.y).toBeCloseTo(c.y-b.y,9);
      }
      expect(result.tracks.get(b.id)?.map(key=>key.interpolation)).toEqual(["linear","linear",curve,curve]);
      expect(nodes).toEqual(before);
    }
  });
  it("allows disjoint static alignment without rewriting either relation",()=>{
    const {a,b,nodes,shape,link}=fixture();
    const d=shape("d",400,800),e=shape("e",850,850);link(e,d,"align_left");
    const result=resolveCanvasRelations([...nodes,e,d],undefined,false,clock);
    expect(result.issue).toBeNull();expect(result.tracks.size).toBe(2);
    expect(result.positions.get(e.id)).toEqual({x:d.x,y:e.y});
    expect(b.relations[0].target_id).toBe(a.id);
  });
  it("refuses ambiguous, unsupported, locked or non-frame-aligned tracks atomically",()=>{
    for(const test of ["cycle","extra","shared","static","mixed","curve","clock","scene-lock","node-lock","missing","hierarchy","beats"]){
      const {a,b,c,nodes,shape,link}=fixture();
      switch(test){
        case "cycle":a.keyframes=[];link(a,c);break;
        case "extra":link(b,a,"align_top");break;
        case "shared":{const d=shape("d",450,800);link(d,a,"align_left");nodes.push(d);break;}
        case "static":a.keyframes=[];break;
        case "mixed":b.keyframes=[...a.keyframes];break;
        case "curve":a.keyframes[2].interpolation="ease_in_out";a.keyframes[3].interpolation="ease_in_out";break;
        case "clock":a.keyframes[2].at={num:"1",den:"31"};a.keyframes[3].at={num:"1",den:"31"};break;
        case "node-lock":b.property_locks=["position"];break;
        case "missing":b.relations[0].target_id="missing";break;
        case "hierarchy":a.parent_id=c.id;break;
      }
      const before=structuredClone(nodes);
      const result=resolveCanvasRelations(nodes,undefined,test==="scene-lock",{...clock,continuous:test!=="beats"});
      expect(result.issue,test).not.toBeNull();expect(result.tracks.size,test).toBe(0);
      expect(result.positions.size,test).toBe(0);expect(nodes).toEqual(before);
    }
  });
});
