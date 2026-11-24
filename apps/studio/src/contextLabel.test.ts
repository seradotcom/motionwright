import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { fixtureProject } from "./fixture";
import { contextLabelNode } from "./contextLabel";
import { resolveCanvasRelations } from "./canvasFollow";
import { previewTransform } from "./canvasMotion";
import { sampleProfileLayout } from "./profileComposition";
import { validateCameraReframe, type CameraReframe } from "./cameraReframe";
import SceneCompositionStudy from "./SceneCompositionStudy";
import type { CanvasKeyframe, CanvasNode } from "./types";

function source(animated=false){
  const project=structuredClone(fixtureProject),scene=project.scenes[0],profile=project.deliverables[0];
  scene.renderer="motion-canvas";scene.beats=[];scene.duration={num:"3",den:"1"};
  scene.camera={...scene.camera,center_x:960,center_y:540,zoom:1,rotation_deg:0};
  profile.width=1920;profile.height=1080;profile.framing_strategy="replan";profile.frame_rate={num:"30",den:"1"};
  const target:CanvasNode={...structuredClone(scene.nodes[0]),kind:"rectangle",name:"Measured output",
    x:720,y:400,width:240,height:160,parent_id:null,rotation_deg:0,opacity:1,
    coordinate_space:"project_pixels",text:null,property_locks:[],relations:[],keyframes:[],
    style:{...scene.nodes[0].style,blend_mode:"normal"}};
  if(animated) for(const [at,x,curve] of [[{num:"0",den:"1"},600,"linear"],[{num:"1",den:"1"},660,"ease_out_cubic"],[{num:"2",den:"1"},720,"hold"]] as const){
    for(const property of ["x","y","width","height","rotation_deg"] as const)
      target.keyframes.push({at,property,value:property==="x"?x:target[property],interpolation:curve});
  }
  scene.nodes=[target];project.scenes=[scene];
  const seed={...structuredClone(target),id:crypto.randomUUID(),z_index:target.z_index+1,kind:"text",text:"Text",
    style:{...target.style,font_size:64,font_family:"Instrument Sans Variable",font_weight:700,line_height:1.05}};
  return {project,scene,target,seed,profile};
}
describe("context labels use existing Canvas identity, source and relation contracts",()=>{
  it("creates a static two-axis label and recomputes it after an anchor edit and on each output aspect",()=>{
    const {project,scene,target,seed,profile}=source(),before=structuredClone(scene);
    const label=contextLabelNode(scene,target,seed,profile);
    expect(label.id).toBe(seed.id);expect(label.text).toBe(target.name);expect(label.keyframes).toEqual([]);
    expect(label.relations.map(relation=>relation.kind)).toEqual(["align_center_x","align_bottom"]);
    expect(new Set(label.relations.map(relation=>relation.id)).size).toBe(2);expect(scene).toEqual(before);
    scene.nodes.push(label);target.x+=120;target.width+=40;
    for(const [width,height] of [[1920,1080],[1080,1920],[1080,1080]]){
      const sampled=sampleProfileLayout(project,scene,{...profile,width,height},0);
      const reference=sampled.nodes.find(node=>node.id===target.id)!,text=sampled.nodes.find(node=>node.id===label.id)!;
      expect(text.x+text.width/2).toBeCloseTo(reference.x+reference.width/2,9);
      expect(text.y+text.height).toBeCloseTo(reference.y+reference.height,9);
      expect(text.id).toBe(label.id);expect(text.text).toBe(label.text);
    }
    expect(label.relations).toHaveLength(2);expect(label.keyframes).toEqual([]);
  });
  it("creates an editable Follow label with its own fade, camera envelope and complete scene study",()=>{
    const {project,scene,target,seed,profile}=source(true);
    const label=contextLabelNode(scene,target,seed,profile);
    label.keyframes=[
      {at:{num:"0",den:"1"},property:"opacity",value:0,interpolation:"linear"},
      {at:{num:"1",den:"1"},property:"opacity",value:1,interpolation:"linear"},
    ] satisfies CanvasKeyframe[];
    label.text="Reviewed label\nOriginal source";
    scene.nodes.push(label);
    const sourceCopy=structuredClone(project);
    const tracks=resolveCanvasRelations(scene.nodes,undefined,false,{duration:scene.duration,fps:profile.frame_rate,continuous:true});
    expect(tracks.issue).toBeNull();expect(label.relations).toHaveLength(1);
    expect(label.relations[0]).toMatchObject({target_id:target.id,kind:"follow"});
    expect(previewTransform({...label,keyframes:tracks.tracks.get(label.id)!},.5)).toMatchObject({x:612.5,opacity:.5});
    const camera:CameraReframe={id:crypto.randomUUID(),scene_id:scene.id,component_version:1,
      start_center_x:920,start_center_y:540,end_center_x:960,end_center_y:540,duration_frames:30};
    expect(()=>validateCameraReframe(camera,scene)).not.toThrow();
    const markup=renderToStaticMarkup(createElement(SceneCompositionStudy,{scene,cameraReframes:[camera],frameRate:profile.frame_rate,
      time:.5,background:"#000000",label:"Context label scene"}));
    expect(markup).toContain("Reviewed label");expect(markup).toContain('opacity="0.5"');
    expect(markup).toContain('x="612.5"');expect(markup).toContain('data-camera-center-x="940"');
    expect(markup).not.toContain('role="alert"');expect(project).toEqual(sourceCopy);
    label.text="Reopened author edit";
    const roundTrip=JSON.parse(JSON.stringify(project));
    const reopened=roundTrip.scenes[0].nodes.find((node:CanvasNode)=>node.id===label.id)!;
    expect(reopened.text).toBe("Reopened author edit");expect(reopened.keyframes.every((key:CanvasKeyframe)=>key.property==="opacity")).toBe(true);
  });
  it("reports invalid relations and clocks rather than drawing a static substitute or altering source",()=>{
    const {scene,target,seed,profile}=source(true),before=structuredClone(scene);
    expect(()=>contextLabelNode(scene,null,seed,profile)).toThrow(/Select/);
    expect(()=>contextLabelNode(scene,target,seed,null)).toThrow(/profile/);
    const label=contextLabelNode(scene,target,seed,profile);
    label.keyframes=[{at:{num:"0",den:"1"},property:"opacity",value:.5,interpolation:"linear"}];
    const markup=renderToStaticMarkup(createElement(SceneCompositionStudy,{scene:{...scene,nodes:[target,label]},
      frameRate:profile.frame_rate,time:0,background:"#000000",label:"Invalid relation"}));
    expect(markup).toContain('role="alert"');expect(markup).not.toContain("<svg");
    expect(scene).toEqual(before);
  });
});
