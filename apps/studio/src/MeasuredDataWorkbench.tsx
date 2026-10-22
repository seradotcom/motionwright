import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Save, Unlink } from "lucide-react";
import type { Change, Project, Scene } from "./types";
import { measuredOutput, parseMeasuredSource } from "./measuredData";
import type { MeasuredView } from "./measuredData";
import { measuredProofMatches, measuredRaw, measuredTable, measuredView, selectMeasuredTable, workbenchScope } from "./creativeWorkbenchDraft";
import type { MeasuredByteProof, MeasuredRaw, WorkbenchInput } from "./creativeWorkbenchDraft";
import { useCreativeWorkbenchDraft, WorkbenchDraftFeedback } from "./useCreativeWorkbenchDraft";

type Props={project:Project;scene:Scene|null;busy:boolean;commit:(change:Change)=>Promise<Project>;onOpenCanvas:()=>void};
const hash=/^[0-9a-f]{64}$/;
const errorMessage=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
export default function MeasuredDataWorkbench(props:Props){
  return <MeasuredEditor key={workbenchScope(props.project,{kind:"measured",sceneId:props.scene?.id??null})} {...props}/>;
}
/** Readonly source preview remains derived from locally reselected exact bytes.
 * Preserved rows are editable-work custody, never recovered byte verification. */
function MeasuredEditor({project,scene,busy,commit,onOpenCanvas}:Props){
  const editor=useCreativeWorkbenchDraft(project,{kind:"measured",sceneId:scene?.id??null},commit,busy);
  const input=editor.draft.input as Extract<WorkbenchInput,{kind:"measured"}>, raw=measuredRaw(input);
  const {label,assetId,rowsText,selectedNode,selectedKey,view,decimals,axisMin,axisMax,maxWidth}=raw;
  const selectedTable=input.selectedTable;
  const patch=(next:Partial<MeasuredRaw>)=>editor.edit({...input,tables:{...input.tables,[selectedTable]:{...raw,...next}}});
  const tables=project.production_design?.measured_tables??[], bindings=project.production_design?.measured_bindings??[];
  const stored=tables.find(table=>table.id===raw.draftId);
  const assets=project.assets.filter(asset=>["application/json","text/csv"].includes(asset.media_type)&&!!asset.content_sha256&&hash.test(asset.content_sha256));
  const currentAsset=assets.find(asset=>asset.id===assetId), sourceValid=!!currentAsset?.content_sha256;
  const [verified,setVerified]=useState<MeasuredByteProof|null>(null),[fileError,setFileError]=useState<string|null>(null);
  const fileEpoch=useRef(0);
  const sourceBytesMatch=measuredProofMatches(editor.draft,project,verified);
  useEffect(()=>{ if(verified&&!measuredProofMatches(editor.draft,project,verified)) setVerified(null); },[verified,editor.draft,project]);
  function selectTable(key:string){
    setVerified(null);setFileError(null);fileEpoch.current++;
    try{editor.edit(selectMeasuredTable(input,key,project));}catch(reason){setFileError(errorMessage(reason));}
  }
  const [tableError,table]=useMemo(()=>{
    try{return [null,measuredTable(raw,project)] as const;}catch(reason){return [errorMessage(reason),null] as const;}
  },[raw,project]);
  async function verifyLocalFile(file:File|undefined){
    const token=editor.authorToken(), started=++fileEpoch.current, source=currentAsset;
    setVerified(null);setFileError(null);
    if(!file)return;
    const current=()=>started===fileEpoch.current&&editor.tokenCurrent(token);
    try{
      if(!source?.content_sha256)throw new Error("Choose an imported JSON/CSV source asset first.");
      if(file.size>64*1024)throw new Error("Measured source exceeds 64 KiB.");
      const bytes=new Uint8Array(await file.arrayBuffer());
      if(!current())return;
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes)),value=>value.toString(16).padStart(2,"0")).join("");
      if(!current())return;
      if(digest!==source.content_sha256)throw new Error("Selected file SHA-256 differs from imported asset. No data is admitted.");
      const rows=parseMeasuredSource(new TextDecoder("utf-8",{fatal:true}).decode(bytes)), rowsText=JSON.stringify(rows,null,2);
      measuredTable({...raw,rowsText},token.project);
      if(!current())return;
      editor.edit({...input,tables:{...input.tables,[selectedTable]:{...raw,rowsText}}});
      setVerified({scope:token.scope,tableKey:selectedTable,assetId:source.id,digest,rowsText});
    }catch(reason){if(current())setFileError(errorMessage(reason));}
  }
  const matchingNodes=scene?.nodes.filter(node=>view==="bar"?node.kind==="rectangle":node.kind==="text")??[];
  const sceneBindings=bindings.filter(binding=>binding.scene_id===scene?.id),availableTable=stored;
  const chosenData=availableTable?.rows.find(row=>row.key===selectedKey);
  const presentation=()=>measuredView(raw);
  const bindError=useMemo(()=>{
    if(!scene||!selectedNode||!chosenData)return "Select a scene, metric row and matching Canvas node.";
    if(!matchingNodes.some(node=>node.id===selectedNode))return "Choose a Canvas node matching the current output kind. The earlier selection remains retained.";
    if(bindings.some(binding=>binding.scene_id===scene.id&&binding.node_id===selectedNode))return "This Canvas node already has a measured binding. Detach it before attaching another.";
    try{measuredOutput(chosenData,presentation());return null;}catch(reason){return errorMessage(reason);}
  },[scene,selectedNode,chosenData,raw,bindings,matchingNodes]);
  const submitTable=async()=>{if(table&&sourceBytesMatch&&!editor.blocked)await editor.send("table",verified);};
  const submitBinding=async()=>{if(!bindError&&!editor.blocked)await editor.send("binding");};
  const detach=async(id:string)=>{if(!editor.blocked)await editor.send({detachBinding:id});};
  return <section className="production-plan" aria-label="Measured data and units">
    <div className="production-section-title">
      <div><h2>Measured data / source-bound graphics</h2>
        <p>Reference a real imported CSV/JSON asset, author typed units, then bind an explicit value to Canvas.</p></div>
    </div>
    <p className="production-help">This editor cannot independently certify the source file's measurements.
      It checks the asset SHA-256 identity and stores a versioned, explicitly declared table snapshot.
      Validate source rights, units and measurement methodology before using data as evidence.</p>
    <WorkbenchDraftFeedback editor={editor} project={project}/>
    {!assets.length && <p role="status">No content-addressed JSON/CSV asset is imported.
      Import a source in Assets before binding metrics; Motionwright will not invent sample values.</p>}
    <div className="production-section-title"><h3>1. Source table</h3></div>
    <div className="production-plan-grid">
      <label className="production-field"><span>Table to update</span>
        <select aria-label="Measured table" value={selectedTable} onChange={e=>selectTable(e.target.value)}><option value="new">New measured table</option>{tables.map(t=>
          <option key={t.id} value={t.id}>{t.label} · v{t.version}</option>)}</select>
      </label>
      <label className="production-field"><span>Imported source asset</span>
        <select aria-label="Measured source asset" value={assetId} onChange={e=>{setVerified(null);setFileError(null);fileEpoch.current++;patch({assetId:e.target.value});}}>
          <option value="">Choose an imported JSON/CSV asset</option>
          {assets.map(a=><option key={a.id} value={a.id}>{a.name} · {a.content_sha256?.slice(0,12)}…</option>)}
        </select>
      </label>
      <label className="production-field"><span>Dataset label</span>
        <input aria-label="Measured dataset label" value={label} maxLength={160} onChange={e=>patch({label:e.target.value})}
          placeholder="Descriptive name of actual source"/></label>
    </div>
    <label className="production-field"><span>Confirm the exact imported source file</span>
      <input aria-label="Measured source file verification" type="file" accept=".json,.csv,application/json,text/csv"
        onChange={e=>void verifyLocalFile(e.target.files?.[0])}/>
      <small>Re-select the actual source bytes. Client SHA-256 and strict row parsing are
        checked before submission; the existing SQLite CAS re-reads the immutable blob independently.</small>
    </label>
    <label className="production-field"><span>Declared numeric rows (read-only source preview)</span>
      <textarea aria-label="Measured numeric rows JSON" rows={8} value={rowsText}
        readOnly spellCheck={false}/>
      <small>Rows are extracted from the matching source file; they cannot be invented or
        silently changed in this form. Allowed units: count, percent, seconds, milliseconds, bytes.</small>
    </label>
    {fileError && <p className="production-error" role="alert">{fileError}</p>}
    {sourceBytesMatch && <p className="production-help" role="status">
      Selected file digest and parsed numeric rows match the imported asset metadata.
      Native Storage will independently verify the exact SHA-256 bytes again.</p>}
    {sourceValid && <p className="mono">SHA-256: {currentAsset!.content_sha256} · Version to save: {stored?stored.version+1:1}</p>}
    {tableError && <p className="production-help" role="status">Source preflight: {tableError}</p>}
    <button className="primary-button" disabled={!table || !sourceBytesMatch || editor.blocked} onClick={submitTable}>
      <Save size={14}/>{stored?"Commit next measured source revision":"Register measured source"}</button>
    <div className="production-section-title"><h3>2. Bind an existing Canvas node</h3></div>
    <p className="production-help">Counters and labels update only text. Bars update only rectangle width;
      the minimum and maximum must come from an explicitly reviewed axis. Styling, positioning,
      colors and measurement units are preserved between data revisions.</p>
    {!scene && <p role="status">Choose a working Motion Canvas scene to bind an existing node.</p>}
    <div className="production-plan-grid">
      <label className="production-field"><span>Typed row</span>
        <select aria-label="Measured datum row" value={selectedKey} onChange={e=>patch({selectedKey:e.target.value})}>
          <option value="">Select an imported datum</option>
          {(availableTable?.rows??[]).map(row=><option key={row.key} value={row.key}>
            {row.label} · {row.unit}</option>)}
        </select></label>
      <label className="production-field"><span>Output</span>
        <select aria-label="Measured output kind" value={view} onChange={e=>{
          patch({view:e.target.value as MeasuredView["kind"]});
        }}>
          <option value="counter">Counter · numeric and unit</option>
          <option value="label">Label · source label and value</option>
          <option value="bar">Bar · explicit min/max width</option>
        </select></label>
      <label className="production-field"><span>Canvas object</span>
        <select aria-label="Measured Canvas node" value={selectedNode} onChange={e=>patch({selectedNode:e.target.value})}>
          <option value="">Choose an existing {view==="bar"?"rectangle":"text"} node</option>
          {selectedNode&&!matchingNodes.some(node=>node.id===selectedNode)&&<option value={selectedNode}>Retained node selection · choose a matching object</option>}
          {matchingNodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}
        </select></label>
      {view!=="bar"?<label className="production-field"><span>Decimal places (0–3)</span>
        <input type="text" inputMode="numeric" aria-label="Measured decimals" value={decimals}
          onChange={e=>patch({decimals:e.target.value})}/></label>:<>
        <label className="production-field"><span>Explicit axis minimum (milli)</span>
          <input aria-label="Measured axis minimum" inputMode="numeric" value={axisMin} onChange={e=>patch({axisMin:e.target.value})}/></label>
        <label className="production-field"><span>Explicit axis maximum (milli)</span>
          <input aria-label="Measured axis maximum" inputMode="numeric" value={axisMax} onChange={e=>patch({axisMax:e.target.value})}/></label>
        <label className="production-field"><span>Maximum bar width (px)</span>
          <input aria-label="Measured maximum bar width" inputMode="numeric" value={maxWidth} onChange={e=>patch({maxWidth:e.target.value})}/></label>
      </>}
    </div>
    {chosenData && !bindError && <p className="mono">Calculated output: {JSON.stringify(measuredOutput(chosenData,presentation()!))}</p>}
    {bindError && <p className="production-help" role="status">{bindError}</p>}
    <div className="production-button-row">
      <button className="primary-button" disabled={!!bindError || editor.blocked}
        onClick={submitBinding}><Save size={14}/> Bind measured output</button>
      <button className="secondary-button" disabled={!scene} onClick={onOpenCanvas}>
        <ExternalLink size={14}/> Edit Canvas objects</button>
    </div>
    {sceneBindings.length>0 && <div className="production-capsule-list">
      {sceneBindings.map(binding=><article key={binding.id}>
        <header><strong>{binding.datum_key} · {binding.view.kind}</strong>
          <button className="secondary-button" disabled={editor.blocked} onClick={()=>detach(binding.id)}>
            <Unlink size={14}/> Detach (keep Canvas)</button></header>
        <p className="mono">Node {binding.node_id.slice(0,8)}… · {JSON.stringify(binding.baseline)}</p>
      </article>)}
    </div>}
    {project.production_design?.last_measured_impact &&
      <p className="production-help" role="status">Latest data revision:
        {" "}{project.production_design.last_measured_impact.changed_keys.length} projected keys changed ·
        {" "}{project.production_design.last_measured_impact.scene_ids.length} dependent scenes ·
        {" "}{project.production_design.last_measured_impact.deliverable_ids.length} dependent output profiles flagged.
        This is a scoped invalidation notice, not proof of a completed render.</p>}
  </section>;
}
