import {useState} from "react";
import type {Project,Scene} from "./types";
import {measuredComparisonRows,measuredComparisonEdits,type MeasuredComparisonInput} from "./measuredComparison";
import {stageDraftMeasuredComparison,type ScopedChangeDraft} from "./scopedChangeDraft";

const fields=[["left","Common left edge",240],["top","First label top",180],["rowSpacing","Row spacing",280],
  ["labelGap","Label-to-bar gap",20],["barHeight","Bar height",48]] as const;
export default function MeasuredComparisonControls({project,scene,draft,disabled,onChange}:{
  project:Project;scene:Scene|null;draft:ScopedChangeDraft;disabled:boolean;onChange:(next:ScopedChangeDraft)=>void;
}){
  const [beforeId,setBefore]=useState(""),[afterId,setAfter]=useState("");
  const [raw,setRaw]=useState<Record<string,string>>(()=>Object.fromEntries(fields.map(([key,,value])=>[key,String(value)])));
  const rows=scene?measuredComparisonRows(project,scene):[];
  let candidate:ScopedChangeDraft|null=null,issue:string|null=null,summary:string|null=null;
  if(scene&&beforeId&&afterId){
    try{
      const values=Object.fromEntries(fields.map(([key])=>{
        if(!/^\d+$/.test(raw[key]))throw new Error("Enter whole source pixels.");
        return [key,Number(raw[key])];
      }));
      const input={beforeId,afterId,...values} as MeasuredComparisonInput;
      const result=measuredComparisonEdits(project,scene,input);
      candidate=stageDraftMeasuredComparison(draft,project,scene,input);
      summary=result.rows.map(row=>row.label.baseline.kind==="text"?row.label.baseline.value:"invalid").join(" / ")+
        ". Common axis: 0 to "+result.axisMaxMilli/1000+" "+result.unit+". Table v"+result.tableVersion+" / "+result.sourceSha.slice(0,12)+".";
    }catch(reason){issue=reason instanceof Error?reason.message:String(reason);}
  }
  return <details className="canvas-arrangement">
    <summary>Measured state comparison</summary>
    <p className="production-help">Lay out two comparable saved measurements on one zero-based bar axis. Each state needs one source-bound label and bar in this scene. Register and bind the original data in Measured data first.</p>
    {rows.length<2&&<p className="production-help" role="status">Two unambiguous label/bar pairs are required. No sample measurements are invented.</p>}
    <div className="production-plan-grid">
      {([["Before state",beforeId,setBefore],["After state",afterId,setAfter]] as const).map(([label,value,set])=>
        <label className="production-field" key={label}><span>{label}</span><select aria-label={label} disabled={disabled} value={value} onChange={e=>set(e.target.value)}>
          <option value="">Choose bound state</option>{rows.map(row=><option key={row.label.id} value={row.label.id}>{row.table.label} / {row.datum.label} ({row.datum.unit})</option>)}</select></label>)}
      {fields.map(([key,label])=><label className="production-field" key={key}><span>{label} (px)</span><input aria-label={"Comparison "+label.toLowerCase()}
        inputMode="numeric" value={raw[key]} disabled={disabled} onChange={e=>setRaw(old=>({...old,[key]:e.target.value}))}/></label>)}
    </div>
    {summary&&<p className="production-help">{summary}</p>}
    {issue&&<p className="production-error" role="status">{issue}</p>}
    <button type="button" className="secondary-button" disabled={disabled||!candidate} onClick={()=>candidate&&onChange(candidate)}>Stage measured comparison</button>
    <p className="production-help">Review the four original object transforms before saving one revision. Values and units stay bound to their source. A later data or layout revision requires a new comparison review; this does not certify the data or maintain a separate managed chart.</p>
  </details>;
}
