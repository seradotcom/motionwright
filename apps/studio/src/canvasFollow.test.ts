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
    for(const test of ["cycle","extra","shared","static","mixed","curve","clock","scene-lock","node-lock","missing","hierarchy","discontinuous-clock"]){
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
      const result=resolveCanvasRelations(nodes,undefined,test==="scene-lock",{...clock,continuous:test!=="discontinuous-clock"});
      expect(result.issue,test).not.toBeNull();expect(result.tracks.size,test).toBe(0);
      expect(result.positions.size,test).toBe(0);expect(nodes).toEqual(before);
    }
  });
});

describe("Follow composition with complete poses and independent text visibility",()=>{
  const properties=["x","y","width","height","rotation_deg"] as const;
  function route(){
    const result=fixture(),{a,b,c}=result;
    a.keyframes=[
      [{num:"0",den:"1"},420,240,"linear"],
      [{num:"1",den:"2"},520,300,"ease_out_cubic"],
      [{num:"1",den:"1"},480,350,"hold"],
      [{num:"3",den:"2"},a.x,a.y,"linear"],
    ].flatMap(([at,x,y,curve])=>properties.map(property=>({
      at:at as {num:string;den:string},property,
      value:property==="x"?x as number:property==="y"?y as number:a[property],
      interpolation:curve as MotionInterpolation,
    })));
    c.kind="text";c.text="Temperature\nMeasured at source";
    b.keyframes=[
      {at:{num:"0",den:"1"},property:"opacity",value:0,interpolation:"linear"},
      {at:{num:"1",den:"3"},property:"opacity",value:1,interpolation:"linear"},
      {at:{num:"1",den:"1"},property:"opacity",value:1,interpolation:"hold"},
      {at:{num:"5",den:"3"},property:"opacity",value:0,interpolation:"ease_out_cubic"},
    ];
    c.keyframes=[
      {at:{num:"0",den:"1"},property:"opacity",value:1,interpolation:"linear"},
      {at:{num:"3",den:"2"},property:"opacity",value:0,interpolation:"linear"},
    ];
    return result;
  }
  it("keeps chained literal offsets through a curved leg, exact Hold jump and linear return, with independent alpha",()=>{
    const {a,b,c,nodes}=route(),before=structuredClone(nodes);
    for(const [w,h] of [[1920,1080],[1080,1080],[1080,1920]]){
      const result=resolveCanvasRelations(nodes,alignmentScale(w,h,"replan"),false,clock);
      expect(result.issue).toBeNull();
      const B={...b,keyframes:result.tracks.get(b.id)!},C={...c,keyframes:result.tracks.get(c.id)!};
      expect(previewTransform(B,.25).x).toBe(807.5);
      expect(previewTransform(B,.999).x).toBe(820);
      expect(previewTransform(B,1).x).toBe(780);
      expect(previewTransform(B,1.25).x).toBe(850);
      for(const t of [0,.125,.25,.5,.999,1,1.25,1.5,1.999]){
        const A=previewTransform(a,t),bt=previewTransform(B,t),ct=previewTransform(C,t);
        expect(bt.x-A.x).toBeCloseTo(300,9);expect(bt.y-A.y).toBeCloseTo(140,9);
        expect(ct.x-bt.x).toBeCloseTo(280,9);expect(ct.y-bt.y).toBeCloseTo(110,9);
        expect(bt.opacity).toBe(previewTransform(b,t).opacity);
        expect(ct.opacity).toBe(previewTransform(c,t).opacity);
        expect(ct.width).toBe(c.width);expect(ct.height).toBe(c.height);expect(ct.rotation_deg).toBe(0);
      }
      expect(C.text).toBe("Temperature\nMeasured at source");
      expect(nodes).toEqual(before);
    }
  });
  it("preserves an exact two-pose held text follower without adding saved keys",()=>{
    const {a,b,nodes}=fixture("hold");b.kind="text";b.text="Step boundary";
    const result=resolveCanvasRelations(nodes,undefined,false,clock);
    expect(result.issue).toBeNull();
    const projected={...b,keyframes:result.tracks.get(b.id)!};
    expect(previewTransform(projected,.999).x).toBe(720);
    expect(previewTransform(projected,1).x).toBe(920);
    expect(b.keyframes).toEqual([]);expect(a.keyframes).toHaveLength(4);
  });
  it("rejects inherited scaling, rotation, mismatched channels, invalid own fades and missing exact opacity clocks",()=>{
    for(const invalid of ["size","rotation","incomplete","mixed_curve","partial_alpha","jump_alpha","own_geometry","off_frame","clock"]){
      const {a,b,nodes}=route();
      switch(invalid){
        case "size":a.keyframes.find(key=>key.property==="width")!.value=200;break;
        case "rotation":a.keyframes.find(key=>key.property==="rotation_deg")!.value=10;break;
        case "incomplete":a.keyframes.splice(3,1);break;
        case "mixed_curve":a.keyframes[5].interpolation="linear";break;
        case "partial_alpha":b.keyframes[1].value=.5;break;
        case "jump_alpha":b.keyframes[1].interpolation="hold";break;
        case "own_geometry":b.keyframes.push({...a.keyframes[0]});break;
        case "off_frame":a.keyframes.slice(5,10).forEach(key=>key.at={num:"1",den:"31"});break;
      }
      const before=structuredClone(nodes);
      const result=resolveCanvasRelations(nodes,undefined,false,invalid==="clock"?undefined:clock);
      expect(result.issue,invalid).not.toBeNull();expect(result.tracks.size,invalid).toBe(0);
      expect(result.positions.size,invalid).toBe(0);expect(nodes).toEqual(before);
    }
  });
});
