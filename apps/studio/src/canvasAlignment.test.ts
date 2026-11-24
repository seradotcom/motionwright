import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { alignmentScale, resolveCanvasAlignments } from "./canvasAlignment";
import type { CanvasNode, RelationKind } from "./types";

function fixture(){
  const seed=structuredClone(fixtureProject.scenes[0].nodes[0]);
  function shape(id:string,x:number,y:number,width:number,height:number):CanvasNode {
    return {...structuredClone(seed),id,kind:"rectangle",parent_id:null,coordinate_space:"project_pixels",
      x,y,width,height,rotation_deg:0,opacity:1,text:null,keyframes:[],relations:[],property_locks:[],
      style:{...seed.style,blend_mode:"normal"}};
  }
  const a=shape("a",650,350,300,200),b=shape("b",1000,430,150,100),c=shape("c",900,460,100,80);
  const link=(node:CanvasNode,target:CanvasNode,kind:RelationKind)=>
    node.relations.push({id:node.id+"-"+kind,target_id:target.id,kind});
  link(b,a,"align_right");link(b,a,"align_bottom");link(c,b,"align_center_x");link(c,b,"align_top");
  return {a,b,c,link,nodes:[c,b,a]};
}
describe("authored static alignment projection",()=>{
  it.each([[1920,1080],[1080,1920],[1080,1080]])("preserves output edges and chained centers at %ix%i", (width,height)=>{
    const {a,b,c,nodes}=fixture(),original=structuredClone(nodes),scale=alignmentScale(width,height,"replan");
    const solved=resolveCanvasAlignments(nodes,scale);
    expect(solved.issue).toBeNull();
    const box=(node:CanvasNode)=>{
      const p=solved.positions.get(node.id)??node;
      return {cx:(p.x+node.width/2)*scale.x,cy:(p.y+node.height/2)*scale.y,
        w:node.width*scale.object,h:node.height*scale.object};
    };
    const A=box(a), B=box(b), C=box(c);
    expect(B.cx+B.w/2).toBeCloseTo(A.cx+A.w/2,8);
    expect(B.cy+B.h/2).toBeCloseTo(A.cy+A.h/2,8);
    expect(C.cx).toBeCloseTo(B.cx,8);
    expect(C.cy-C.h/2).toBeCloseTo(B.cy-B.h/2,8);
    expect(nodes).toEqual(original);
    a.x+=60;a.y+=20;
    expect(resolveCanvasAlignments(nodes,scale).positions.get(b.id)!.x).toBeCloseTo(solved.positions.get(b.id)!.x+60,8);
    expect(resolveCanvasAlignments(nodes,scale).positions.get(c.id)!.y).toBeCloseTo(solved.positions.get(c.id)!.y+20,8);
  });
  it("supports all six relations and leaves the unconstrained axis untouched",()=>{
    const {a,b,link}=fixture();
    for (const [kind,x,y] of [
      ["align_left",a.x,b.y],["align_center_x",a.x+(a.width-b.width)/2,b.y],
      ["align_right",a.x+a.width-b.width,b.y],["align_top",b.x,a.y],
      ["align_center_y",b.x,a.y+(a.height-b.height)/2],["align_bottom",b.x,a.y+a.height-b.height],
    ] as const) {
      b.relations=[];link(b,a,kind);
      expect(resolveCanvasAlignments([a,b]).positions.get(b.id)).toEqual({x,y});
    }
  });
  it("rejects cycles, competing axes, follow, animated targets and position locks without partial results",()=>{
    for(const kind of ["cycle","competing","follow","animation","locked","scene-lock","parent","missing"]){
      const {a,b,c,link,nodes}=fixture();
      switch(kind){
        case "cycle":link(a,b,"align_left");break;
        case "competing":link(b,c,"align_left");break;
        case "follow":b.relations=[];link(b,a,"follow");break;
        case "animation":a.keyframes=[{at:{num:"0",den:"1"},property:"x",value:a.x,interpolation:"linear"}];break;
        case "locked":b.property_locks=["position"];break;
        case "parent":a.parent_id=c.id;break;
        case "missing":b.relations[0].target_id="missing";break;
      }
      const before=structuredClone(nodes);
      const result=resolveCanvasAlignments(nodes,undefined,kind==="scene-lock");
      expect(result.issue,kind).not.toBeNull();
      expect(result.positions.size,kind).toBe(0);
      expect(nodes).toEqual(before);
    }
  });
  it("does not rewrite a locked node already matching its target",()=>{
    const {a,b,link}=fixture();b.x=a.x;b.relations=[];link(b,a,"align_left");b.property_locks=["position"];
    const solved=resolveCanvasAlignments([a,b]);
    expect(solved.issue).toBeNull();expect(solved.positions.get(b.id)).toEqual({x:b.x,y:b.y});
  });
});
