import type { CanvasKeyframe, CanvasNode, DeliverableProfile, Project, RationalTime, Scene } from "./types";
import type { ScopedCanvasEdit } from "./creativeProduction";
import { exactCanvasOpacitySpans } from "./canvasOpacity";
import { sameValue } from "./creativeProduction";

export const FOCUS_TRANSFER_ID = "motionwright.transition.focus-transfer";
export const FOCUS_TRANSFER_VERSION = 1;
export interface FocusTransferInput {
  fromId:string; toId:string; startFrame:number; endFrame:number; curve:"linear"|"ease_out_cubic";
}
type MotionEdit = Extract<ScopedCanvasEdit,{kind:"motion"}>;

export function recipeFrameTime(frame:number,fps:RationalTime):RationalTime {
  if (!/^[1-9][0-9]{0,18}$/.test(fps.num) || !/^[1-9][0-9]{0,18}$/.test(fps.den))
    throw new Error("Choose a saved positive rational frame rate.");
  const n=BigInt(frame)*BigInt(fps.den),d=BigInt(fps.num);
  let a=n,b=d;
  while(b) [a,b]=[b,a%b];
  return {num:String(n/a),den:String(d/a)};
}

/** Exact authored visibility transfer, not a raster dissolve or auto-layout.
 * Existing motion, hierarchy, measured/generated objects and relation writers
 * remain untouched. The existing CAS patch route owns the actual mutation. */
export function focusTransferEdits(project:Project,scene:Scene,profile:DeliverableProfile,input:FocusTransferInput): {edits:MotionEdit[];rationale:string} {
  if(!project.scenes.some(item=>sameValue(item,scene)) || scene.renderer!=="motion-canvas"
    || !project.deliverables.some(item=>sameValue(item,profile))
    || (profile.included_scene_ids.length>0 && !profile.included_scene_ids.includes(scene.id)))
    throw new Error("Focus transfer needs a current Motion Canvas scene in the saved output profile.");
  if(!Number.isInteger(input.startFrame)||!Number.isInteger(input.endFrame)||input.startFrame<0||
     input.endFrame<=input.startFrame||input.endFrame>36000||!["linear","ease_out_cubic"].includes(input.curve))
    throw new Error("Choose a nonempty interval of at most 36,000 exact frames.");
  if(input.fromId===input.toId)throw new Error("Choose two different authored objects.");
  const ids=new Set([input.fromId,input.toId]);
  const sources=[...ids].map(id=>scene.nodes.find(node=>node.id===id));
  const managed=new Set([
    ...(project.production_design?.heroes??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.stages??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.procedural_fields??[]).flatMap(item=>item.baseline.map(node=>node.id)),
    ...(project.production_design?.measured_bindings??[]).map(item=>item.node_id),
  ]);
  if(project.locks.some(lock=>["project:"+project.id,"scene:"+scene.id].includes(lock.resource)&&
    ["content","style","position","timing","renderer"].includes(lock.kind)))
    throw new Error("The original scene is locked.");
  if(sources.some(node=>!node||managed.has(node.id)||!["shape","rectangle","circle"].includes(node.kind)||
    node.parent_id!==null||node.coordinate_space!=="project_pixels"||node.rotation_deg!==0||
    node.opacity!==1||node.style.blend_mode!=="normal"||node.keyframes.length>0||node.relations.length>0||
    node.property_locks.includes("opacity")||node.width<=0||node.height<=0||
    ![node.x,node.y,node.width,node.height].every(Number.isFinite)) ||
    scene.nodes.some(node=>(node.parent_id!==null&&ids.has(node.parent_id))||node.relations.some(relation=>ids.has(relation.target_id))))
    throw new Error("Use two independent, unanimated, unlocked original shapes with normal blend. Managed components, groups and related objects are not replaced.");
  const [from,to]=sources as [CanvasNode,CanvasNode];
  const clock=profile.frame_rate;
  const start=recipeFrameTime(input.startFrame,clock),end=recipeFrameTime(input.endFrame,clock);
  const edits:MotionEdit[]=[from,to].map((node,index)=>{
    const value=index===0?1:0;
    const keyframes:CanvasKeyframe[]=[{at:{num:"0",den:"1"},property:"opacity",value,interpolation:"linear"}];
    if(input.startFrame>0)keyframes.push({at:start,property:"opacity",value,interpolation:"hold"});
    keyframes.push({at:end,property:"opacity",value:1-value,interpolation:input.curve});
    const spans=exactCanvasOpacitySpans({...node,keyframes},scene,clock);
    if(spans?.length!==1||!sameValue(spans[0].start,start)||!sameValue(spans[0].end,end))
      throw new Error("The interval is not an exact native opacity transfer.");
    return {kind:"motion",node_id:node.id,keyframes};
  });
  // Every affected saved output uses its own exact clock. Do not create
  // fractional-frame changes in another variant silently.
  for(const target of project.deliverables.filter(item=>!item.included_scene_ids.length||item.included_scene_ids.includes(scene.id))){
    for(const edit of edits){
      const node=scene.nodes.find(item=>item.id===edit.node_id)!;
      try { exactCanvasOpacitySpans({...node,keyframes:edit.keyframes},scene,target.frame_rate); }
      catch { throw new Error("Focus transfer is not frame-aligned in output "+target.name+". Choose an interval shared by the saved rates."); }
    }
  }
  return {edits,rationale:FOCUS_TRANSFER_ID+"/v1: authored attention transfer from "+from.name+" to "+to.name+
    ", frames "+input.startFrame+"-"+input.endFrame+" at "+clock.num+"/"+clock.den+", "+input.curve+"."};
}
