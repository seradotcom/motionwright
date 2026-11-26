import type {CanvasKeyframe,CanvasNode,DeliverableProfile,Project,RationalTime,Scene} from "./types";
import {sameValue,type ScopedCanvasEdit} from "./creativeProduction";
import {recipeFrameTime} from "./focusTransfer";
import {nativePoseEdit,type PoseGeometry} from "./nativePoseDraft";
import {isStaticNativeGroup,nativeGroupOrderIssue} from "./nativeGroupHierarchy";

export const MASK_WINDOW_ID="motionwright.transition.mask-window";
export interface MaskWindowInput {
  childId:string;direction:"left"|"right"|"top"|"bottom";mode:"reveal"|"conceal";
  gap:number;startFrame:number;endFrame:number;curve:"linear"|"ease_out_cubic";
}
type MotionEdit=Extract<ScopedCanvasEdit,{kind:"motion"}>;
type Box=Pick<CanvasNode,"x"|"y"|"width"|"height">;
export interface MaskWindowResult {
  edits:MotionEdit[];rationale:string;clipId:string;initial:PoseGeometry;terminal:PoseGeometry;
}
const geometry=(node:CanvasNode):PoseGeometry=>({x:node.x,y:node.y,width:node.width,height:node.height,rotation_deg:node.rotation_deg});
const finiteBox=(node:Box)=>[node.x,node.y,node.width,node.height].every(Number.isFinite)&&node.width>0&&node.height>0;
function projected(box:Box,width:number,height:number):Box {
  const sx=width/1920,sy=height/1080,scale=Math.min(sx,sy);
  const w=box.width*scale,h=box.height*scale;
  return {x:(box.x+box.width/2)*sx-w/2,y:(box.y+box.height/2)*sy-h/2,width:w,height:h};
}
const contained=(inner:Box,outer:Box)=>inner.x>=outer.x-1e-6&&inner.y>=outer.y-1e-6&&
  inner.x+inner.width<=outer.x+outer.width+1e-6&&inner.y+inner.height<=outer.y+outer.height+1e-6;
function separated(inner:Box,outer:Box,side:MaskWindowInput["direction"]):boolean{
  switch(side){
    case "left":return inner.x+inner.width<outer.x-1e-6;
    case "right":return inner.x>outer.x+outer.width+1e-6;
    case "top":return inner.y+inner.height<outer.y-1e-6;
    case "bottom":return inner.y>outer.y+outer.height+1e-6;
  }
}
function frameAt(time:RationalTime,profile:DeliverableProfile):number{
  const {num,den}=profile.frame_rate;
  if(!/^[1-9][0-9]{0,18}$/.test(num)||!/^[1-9][0-9]{0,18}$/.test(den))
    throw new Error("Use a valid saved rational frame clock.");
  const n=BigInt(time.num)*BigInt(num),d=BigInt(time.den)*BigInt(den);
  if(d<=0n||n<0n||n%d!==0n||n/d>36000n)
    throw new Error("Mask window is not frame-aligned in output "+profile.name+". Choose a shared interval.");
  return Number(n/d);
}

/** Reveal or conceal a real original child using its unchanged Clip plane.
 * Both endpoints are exact under every selected output's actual Replan
 * projection. No alpha track, animated synthetic mask, or flattened source. */
export function maskWindowEdits(project:Project,scene:Scene,profile:DeliverableProfile,input:MaskWindowInput):MaskWindowResult{
  if(!project.scenes.some(item=>sameValue(item,scene))||scene.renderer!=="motion-canvas"||
    !project.deliverables.some(item=>sameValue(item,profile))||
    (profile.included_scene_ids.length&&!profile.included_scene_ids.includes(scene.id)))
    throw new Error("Mask window needs a current saved Motion Canvas scene and output profile.");
  if(!["left","right","top","bottom"].includes(input.direction)||!["reveal","conceal"].includes(input.mode)||
    !["linear","ease_out_cubic"].includes(input.curve)||!Number.isSafeInteger(input.gap)||input.gap<1||input.gap>240||
    !Number.isSafeInteger(input.startFrame)||!Number.isSafeInteger(input.endFrame)||input.startFrame<0||
    input.endFrame<=input.startFrame||input.endFrame>36000)
    throw new Error("Choose a supported direction, a positive gap of at most 240 source pixels and an exact nonempty frame interval.");
  if(scene.camera.center_x!==960||scene.camera.center_y!==540||scene.camera.zoom!==1||scene.camera.rotation_deg!==0||
    project.production_design?.camera_reframes?.some(item=>item.scene_id===scene.id))
    throw new Error("Mask window requires the neutral original camera.");
  if(project.locks.some(lock=>["project:"+project.id,"scene:"+scene.id].includes(lock.resource)&&
    ["content","position","timing","style","renderer"].includes(lock.kind)))
    throw new Error("The original scene is locked.");
  const child=scene.nodes.find(node=>node.id===input.childId);
  const clip=scene.nodes.find(node=>node.id===child?.parent_id);
  const managed=new Set([
    ...(project.production_design?.heroes??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.stages??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.procedural_fields??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.measured_bindings??[]).map(item=>item.node_id),
  ]);
  if(!child||!clip||clip.kind!=="clip_group"||clip.parent_id!==null||!isStaticNativeGroup(clip)||
    !finiteBox(clip)||clip.style.fill!==null||clip.style.stroke!==null||clip.style.stroke_width!==0||
    managed.has(clip.id)||managed.has(child.id)||
    !["shape","rectangle","circle"].includes(child.kind)||child.coordinate_space!=="project_pixels"||
    child.opacity!==1||child.rotation_deg!==0||child.keyframes.length||child.relations.length||
    child.style.blend_mode!=="normal"||child.style.stroke!==null||child.style.stroke_width!==0||
    !/^#[0-9a-f]{6}$/i.test(child.style.fill??"")||!finiteBox(child)||
    child.property_locks.some(lock=>["position","size","rotation"].includes(lock))||
    scene.nodes.some(node=>node.parent_id===child.id||node.relations.some(relation=>[child.id,clip.id].includes(relation.target_id))))
    throw new Error("Choose an unlocked, unanimated, opaque filled original shape directly inside an unchanged root Clip. No managed objects, strokes, relation or hierarchy writers.");
  const hierarchyIssue=nativeGroupOrderIssue(scene);
  if(hierarchyIssue)throw new Error(hierarchyIssue);
  const terminal=geometry(child),initial={...terminal};
  if(input.mode==="reveal"){
    switch(input.direction){
      case "left":initial.x=clip.x-child.width-input.gap;break;
      case "right":initial.x=clip.x+clip.width+input.gap;break;
      case "top":initial.y=clip.y-child.height-input.gap;break;
      case "bottom":initial.y=clip.y+clip.height+input.gap;break;
    }
  }else{
    if(input.direction==="left"||input.direction==="right")initial.x=clip.x+(clip.width-child.width)/2;
    else initial.y=clip.y+(clip.height-child.height)/2;
  }
  if(!finiteBox(initial)||sameValue(initial,terminal))
    throw new Error("Mask window requires two distinct usable source poses.");
  // The entire original moving geometry stays inside the deterministic output
  // envelope even when the Clip hides it. Never rely on clipping to excuse an
  // unadmitted off-canvas path.
  const verify=(width:number,height:number,margin:number,label:string)=>{
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<320||height<320||
      width>7680||height>7680||!Number.isFinite(margin)||margin<0||margin>=.5)
      throw new Error("Invalid safe-area output "+label+".");
    const outer=projected(clip,width,height),a=projected(initial,width,height),b=projected(terminal,width,height);
    const safe={x:width*margin,y:height*margin,width:width*(1-2*margin),height:height*(1-2*margin)};
    if(![outer,a,b].every(box=>contained(box,safe)))
      throw new Error("Mask window leaves the deterministic safe area in "+label+".");
    const visible=input.mode==="reveal"?b:a,hidden=input.mode==="reveal"?a:b;
    if(!contained(visible,outer)||!separated(hidden,outer,input.direction))
      throw new Error("Mask window needs one fully visible pose and one fully clipped pose on the chosen side in "+label+".");
  };
  verify(1920,1080,Math.max(.05,scene.camera.safe_margin),"original Canvas");
  const start=recipeFrameTime(input.startFrame,profile.frame_rate),end=recipeFrameTime(input.endFrame,profile.frame_rate);
  const durationN=BigInt(scene.duration.num),durationD=BigInt(scene.duration.den);
  if(durationN<=0n||durationD<=0n||BigInt(end.num)*durationD>=durationN*BigInt(end.den))
    throw new Error("The last reveal or conceal frame must lie strictly inside the scene.");
  const profiles=project.deliverables.filter(item=>!item.included_scene_ids.length||item.included_scene_ids.includes(scene.id));
  if(!profiles.length)throw new Error("Include this scene in a saved output.");
  let keyframes:CanvasKeyframe[]|null=null;
  for(const output of profiles){
    if(output.framing_strategy!=="replan")throw new Error("Mask window requires exact Replan framing: "+output.name+".");
    // Native uses the maximum safe margin of the selected Film segment.
    // The full selected cut is a conservative upper bound on that margin.
    const selected=project.scenes.filter(item=>!output.included_scene_ids.length||output.included_scene_ids.includes(item.id));
    const margin=Math.max(.05,...selected.map(item=>item.camera.safe_margin));
    verify(output.width,output.height,margin,output.name);
    const a=frameAt(start,output),b=frameAt(end,output);
    if(a===0){
      // Legacy paired X/Y Settle is the exact native path for immediate motion.
      const keys:CanvasKeyframe[]=[{at:{num:"0",den:"1"},property:"x",value:initial.x,interpolation:"linear"},
        {at:{num:"0",den:"1"},property:"y",value:initial.y,interpolation:"linear"},
        {at:end,property:"x",value:terminal.x,interpolation:input.curve},
        {at:end,property:"y",value:terminal.y,interpolation:input.curve}];
      if(keyframes&&!sameValue(keyframes,keys))throw new Error("Mask window changes its source clock between outputs.");
      keyframes=keys;
    }else{
      // A constant first interval and a second literal pose tween. The existing
      // exact complete-pose grammar handles both intervals on this Clip child.
      const result=nativePoseEdit(project,scene,child,output,{start:initial,endFrame:b,curve:input.curve,
        fade:"none",fadeCurve:"linear",waypoints:[{frame:a,pose:initial,curve:"linear"}]});
      if(!result.keys)throw new Error(result.issue);
      if(keyframes&&!sameValue(keyframes,result.keys))throw new Error("Mask window changes its source clock between outputs.");
      keyframes=result.keys;
    }
  }
  if(!keyframes)throw new Error("No exact output clock admitted the original motion.");
  const rationale=MASK_WINDOW_ID+"/v1: "+input.mode+" original "+child.id+" through Clip "+clip.id+
    " from/to "+input.direction+", frames "+input.startFrame+"-"+input.endFrame+" at "+
    profile.frame_rate.num+"/"+profile.frame_rate.den+", "+input.curve+
    ". Retain source parent, layer order, terminal geometry and full opacity.";
  return {edits:[{kind:"motion",node_id:child.id,keyframes}],rationale,clipId:clip.id,initial,terminal};
}
