import {useState} from "react";
import type {DeliverableProfile,Project,Scene} from "./types";
import type {MaskWindowInput} from "./maskWindow";
import {stageDraftMaskWindow,type ScopedChangeDraft} from "./scopedChangeDraft";

export default function MaskWindowControls({project,scene,profile,draft,disabled,onChange}:{
  project:Project;scene:Scene|null;profile:DeliverableProfile|null;draft:ScopedChangeDraft;disabled:boolean;
  onChange:(next:ScopedChangeDraft)=>void;
}){
  const [childId,setChild]=useState(""),[mode,setMode]=useState<MaskWindowInput["mode"]>("reveal");
  const [direction,setDirection]=useState<MaskWindowInput["direction"]>("left");
  const [curve,setCurve]=useState<MaskWindowInput["curve"]>("linear");
  const [gap,setGap]=useState("16"),[start,setStart]=useState("15"),[end,setEnd]=useState("45");
  const nodes=scene?.nodes.filter(node=>["shape","rectangle","circle"].includes(node.kind)&&
    scene.nodes.some(parent=>parent.id===node.parent_id&&parent.kind==="clip_group"&&parent.parent_id===null))??[];
  let candidate:ScopedChangeDraft|null=null,issue:string|null=null;
  if(childId){
    try{
      if(!(mode==="reveal"?[gap,start,end]:[start,end]).every(raw=>/^\d{1,6}$/.test(raw)))throw new Error("Enter whole source pixels and frame indices.");
      candidate=stageDraftMaskWindow(draft,project,scene,profile,{childId,mode,direction,curve,
        gap:mode==="reveal"?Number(gap):16,startFrame:Number(start),endFrame:Number(end)});
    }catch(reason){issue=reason instanceof Error?reason.message:String(reason);}
  }
  return <details className="canvas-arrangement">
    <summary>Mask window</summary>
    <p className="production-help">Move an original shape through its saved rectangular Clip. Reveal begins fully hidden and ends at the saved visible pose. Conceal starts inside the Clip and ends at the saved hidden pose. The Clip never changes position, and opacity is not animated.</p>
    <div className="production-plan-grid">
      <label className="production-field"><span>Original Clip child</span><select aria-label="Mask window child" value={childId} disabled={disabled} onChange={event=>setChild(event.target.value)}>
        <option value="">Choose an original child</option>{nodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
      <label className="production-field"><span>Movement</span><select aria-label="Mask window mode" value={mode} disabled={disabled} onChange={event=>setMode(event.target.value as MaskWindowInput["mode"])}>
        <option value="reveal">Reveal</option><option value="conceal">Conceal</option></select></label>
      <label className="production-field"><span>Hidden side</span><select aria-label="Mask window direction" value={direction} disabled={disabled} onChange={event=>setDirection(event.target.value as MaskWindowInput["direction"])}>
        {["left","right","top","bottom"].map(side=><option key={side} value={side}>{side}</option>)}</select></label>
      <label className="production-field"><span>Reveal start gap (px)</span><input aria-label="Mask window gap" inputMode="numeric" value={gap} disabled={disabled||mode==="conceal"} onChange={event=>setGap(event.target.value)}/></label>
      <label className="production-field"><span>Start frame</span><input aria-label="Mask window start frame" inputMode="numeric" value={start} disabled={disabled} onChange={event=>setStart(event.target.value)}/></label>
      <label className="production-field"><span>End frame</span><input aria-label="Mask window end frame" inputMode="numeric" value={end} disabled={disabled} onChange={event=>setEnd(event.target.value)}/></label>
      <label className="production-field"><span>Curve</span><select aria-label="Mask window curve" value={curve} disabled={disabled} onChange={event=>setCurve(event.target.value as MaskWindowInput["curve"])}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Ease out cubic</option></select></label>
    </div>
    {!nodes.length&&<p className="production-help">Create a Clip group in Canvas, parent an original shape to it and set its terminal geometry first.</p>}
    <p className="production-help">{profile?profile.name+" at "+profile.frame_rate.num+"/"+profile.frame_rate.den+" fps.":"Choose a saved output profile."}
      {" "}Both poses must retain the complete original object inside each Replan output's safe area. Existing motion, hierarchy and source bindings are preserved.</p>
    {issue&&<p className="production-error" role="status">{issue}</p>}
    <button type="button" className="secondary-button" disabled={disabled||!candidate} onClick={()=>candidate&&onChange(candidate)}>Stage mask window</button>
    <p className="production-help">Scrub the before/after preview and explicitly save the original motion track. Later Clip or profile changes require a new review. Editorial preview is not Native pixel evidence.</p>
  </details>;
}
