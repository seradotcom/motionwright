import { useState } from "react";
import type { DeliverableProfile, Project, Scene } from "./types";
import { stageDraftFocusTransfer, type ScopedChangeDraft } from "./scopedChangeDraft";
import type { FocusTransferInput } from "./focusTransfer";

export default function FocusTransferControls({project,scene,profile,draft,disabled,onChange}:{
  project:Project;scene:Scene|null;profile:DeliverableProfile|null;draft:ScopedChangeDraft;disabled:boolean;
  onChange:(next:ScopedChangeDraft)=>void;
}){
  const [fromId,setFrom]=useState(""),[toId,setTo]=useState(""),[start,setStart]=useState("0"),[end,setEnd]=useState("15");
  const [curve,setCurve]=useState<FocusTransferInput["curve"]>("ease_out_cubic");
  const nodes=scene?.nodes.filter(node=>["shape","rectangle","circle"].includes(node.kind))??[];
  let candidate:ScopedChangeDraft|null=null,issue:string|null=null;
  if(fromId&&toId){
    try{
      if(!/^\d+$/.test(start)||!/^\d+$/.test(end))throw new Error("Enter whole frame indices.");
      candidate=stageDraftFocusTransfer(draft,project,scene,profile,{fromId,toId,startFrame:Number(start),endFrame:Number(end),curve});
    }catch(reason){issue=reason instanceof Error?reason.message:String(reason);}
  }
  return <details className="canvas-arrangement">
    <summary>Focus transfer</summary>
    <p className="production-help">Two original objects exchange visibility over one exact frame interval. The first fades out and the second fades in; there is no new renderer or rasterized replacement.</p>
    <div className="production-plan-grid">
      <label className="production-field"><span>From object</span><select aria-label="Focus transfer from" value={fromId} disabled={disabled} onChange={e=>setFrom(e.target.value)}>
        <option value="">Choose an object</option>{nodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
      <label className="production-field"><span>To object</span><select aria-label="Focus transfer to" value={toId} disabled={disabled} onChange={e=>setTo(e.target.value)}>
        <option value="">Choose an object</option>{nodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}</select></label>
      <label className="production-field"><span>Start frame</span><input aria-label="Focus transfer start frame" inputMode="numeric" value={start} disabled={disabled} onChange={e=>setStart(e.target.value)}/></label>
      <label className="production-field"><span>End frame</span><input aria-label="Focus transfer end frame" inputMode="numeric" value={end} disabled={disabled} onChange={e=>setEnd(e.target.value)}/></label>
      <label className="production-field"><span>Curve</span><select aria-label="Focus transfer curve" value={curve} disabled={disabled} onChange={e=>setCurve(e.target.value as FocusTransferInput["curve"])}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Ease out cubic</option></select></label>
    </div>
    <p className="production-help">{profile?profile.name+" at "+profile.frame_rate.num+"/"+profile.frame_rate.den+" fps":"Choose a saved delivery profile."} Only unanimated independent original shapes are accepted. No existing motion or managed component is overwritten.</p>
    {issue&&<p className="production-error" role="status">{issue}</p>}
    <button type="button" className="secondary-button" disabled={disabled||!candidate} onClick={()=>candidate&&onChange(candidate)}>Stage focus transfer</button>
    <p className="production-help">Review the staged motion with the existing before/after proposal, then explicitly apply it. Staging changes no saved object.</p>
  </details>;
}
