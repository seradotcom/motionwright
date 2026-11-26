import {useState} from "react";
import type {DeliverableProfile,Project,Scene} from "./types";
import {measuredSeriesRevealEdits,type MeasuredSeriesRevealInput} from "./measuredSeriesReveal";
import {stageDraftMeasuredSeriesReveal,type ScopedChangeDraft} from "./scopedChangeDraft";
const fields=[["left","Common left edge (px)",240],["top","First label top (px)",120],["rowSpacing","Row spacing (px)",210],
  ["labelGap","Label-to-bar gap (px)",16],["barHeight","Bar height (px)",40],
  ["startFrame","Start frame",0],["stepFrames","Frames between reveals",30],["fadeFrames","Fade frames",15]] as const;
export default function MeasuredSeriesRevealControls({project,scene,profile,draft,disabled,onChange}:{
  project:Project;scene:Scene|null;profile:DeliverableProfile|null;draft:ScopedChangeDraft;disabled:boolean;onChange:(draft:ScopedChangeDraft)=>void;
}){
  const [tableId,setTable]=useState(""),[curve,setCurve]=useState<MeasuredSeriesRevealInput["curve"]>("linear");
  const [raw,setRaw]=useState<Record<string,string>>(()=>Object.fromEntries(fields.map(([key,,value])=>[key,String(value)])));
  const tables=project.production_design?.measured_tables??[];
  let candidate:ScopedChangeDraft|null=null,issue:string|null=null,summary:string|null=null;
  if(scene&&profile&&tableId){
    try{
      const values=Object.fromEntries(fields.map(([key])=>{
        if(!/^\d+$/.test(raw[key]))throw new Error("Enter whole source pixels and frame indices.");
        return [key,Number(raw[key])];
      }));
      const input={tableId,curve,...values} as MeasuredSeriesRevealInput;
      const result=measuredSeriesRevealEdits(project,scene,profile,input);
      candidate=stageDraftMeasuredSeriesReveal(draft,project,scene,profile,input);
      summary=result.intervals.map(interval=>interval.date+" (frames "+interval.startFrame+" to "+interval.endFrame+")").join(" / ")+
        ". Common axis: 0 to "+result.axisMaxMilli/1000+" "+result.unit+". Source table v"+result.tableVersion+" / "+result.sourceSha.slice(0,12)+".";
    }catch(reason){issue=reason instanceof Error?reason.message:String(reason);}
  }
  return <details className="canvas-arrangement">
    <summary>Measured series reveal</summary>
    <p className="production-help">Reveal every dated measurement in a saved table in calendar order. All original label/bar pairs use one zero-based axis. The reveal frames are editorial timing, not a continuous elapsed-time axis.</p>
    <div className="production-plan-grid">
      <label className="production-field"><span>Saved dated source table</span>
        <select aria-label="Series source table" value={tableId} disabled={disabled} onChange={e=>setTable(e.target.value)}>
          <option value="">Choose a saved table</option>{tables.map(table=><option key={table.id} value={table.id}>{table.label} / v{table.version}</option>)}</select></label>
      {fields.map(([key,label])=><label className="production-field" key={key}><span>{label}</span>
        <input aria-label={"Series "+label.toLowerCase()} inputMode="numeric" value={raw[key]} disabled={disabled}
          onChange={e=>setRaw(old=>({...old,[key]:e.target.value}))}/></label>)}
      <label className="production-field"><span>Curve</span><select aria-label="Series reveal curve" disabled={disabled} value={curve}
        onChange={e=>setCurve(e.target.value as MeasuredSeriesRevealInput["curve"])}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Ease out cubic</option></select></label>
    </div>
    <p className="production-help">{profile?profile.name+" at "+profile.frame_rate.num+"/"+profile.frame_rate.den+" fps":"Choose a saved output profile."}
      {" "}Two to four unique ISO-dated rows, each bound to its original label and rectangle, are required. No values, dates or ranges are invented.</p>
    {summary&&<p className="production-help">{summary}</p>}
    {issue&&<p className="production-error" role="status">{issue}</p>}
    <button className="secondary-button" type="button" disabled={disabled||!candidate} onClick={()=>candidate&&onChange(candidate)}>Stage series reveal</button>
    <p className="production-help">Review every label and bar at the same scene playhead, then explicitly save. Later source/layout changes require a fresh series review; no separate managed chart or physical renderer acceptance is implied.</p>
  </details>;
}
