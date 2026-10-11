import { useEffect, useMemo, useState } from "react";
import { ExternalLink, RefreshCw, Save, Unlink } from "lucide-react";
import type { Change, Project, Scene } from "./types";
import {
  measuredOutput, validateMeasuredTable
} from "./measuredData";
import type { MeasuredTable, MeasuredDatum, MeasuredView } from "./measuredData";

type Props={project:Project;scene:Scene|null;busy:boolean;commit:(c:Change)=>Promise<void>;onOpenCanvas:()=>void};
const tableSchema="motionwright.measured-table/1";
const hash=/^[0-9a-f]{64}$/;
const newTable=():Pick<MeasuredTable,"id"|"label"|"version"|"rows">=>({
  id:crypto.randomUUID(),label:"",version:1,rows:[]
});
function errorMessage(err:unknown):string{return err instanceof Error?err.message:String(err);}

/** No generated metrics, no inferred axis, no inline remote fetch.
 * The only candidate sources are already-imported, content-addressed assets.
 */
export default function MeasuredDataWorkbench({project,scene,busy,commit,onOpenCanvas}:Props){
  const tables=project.production_design?.measured_tables??[];
  const bindings=project.production_design?.measured_bindings??[];
  const assets=project.assets.filter(a=>
    ["application/json","text/csv"].includes(a.media_type) && !!a.content_sha256 && hash.test(a.content_sha256));
  const [selectedTable,setSelectedTable]=useState<string>("new");
  const stored=tables.find(t=>t.id===selectedTable);
  const [draftId,setDraftId]=useState(()=>newTable().id);
  const [label,setLabel]=useState("");
  const [assetId,setAssetId]=useState("");
  const [rowsText,setRowsText]=useState("[]");
  const [base,setBase]=useState(project.revision);
  const [error,setError]=useState<string|null>(null);
  const [selectedNode,setSelectedNode]=useState("");
  const [selectedKey,setSelectedKey]=useState("");
  const [view,setView]=useState<MeasuredView["kind"]>("counter");
  const [decimals,setDecimals]=useState(2);
  // Empty on purpose. A bar cannot infer its zero point or ceiling.
  const [axisMin,setAxisMin]=useState("");
  const [axisMax,setAxisMax]=useState("");
  const [maxWidth,setMaxWidth]=useState("");
  const currentAsset=assets.find(a=>a.id===assetId);
  const stale=base!==project.revision;
  const sourceValid=!!currentAsset?.content_sha256;
  const matchingNodes=scene?.nodes.filter(node=>view==="bar"?node.kind==="rectangle":node.kind==="text")??[];
  const sceneBindings=bindings.filter(binding=>binding.scene_id===scene?.id);
  const reset=()=>{
    setDraftId(crypto.randomUUID());setSelectedTable("new");setLabel("");setRowsText("[]");
    setAssetId("");setBase(project.revision);setError(null);
  };
  const load=()=>{
    setLabel(stored?.label??"");
    setRowsText(JSON.stringify(stored?.rows??[],null,2));
    setAssetId(stored?.source_asset_id??"");
    setBase(project.revision);setError(null);
  };
  useEffect(()=>{
    setSelectedTable("new");setDraftId(crypto.randomUUID());setLabel("");setRowsText("[]");
    setAssetId("");setBase(project.revision);setError(null);
    // A different Project generation must not reuse an earlier source draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation]);
  const parsed=useMemo(()=>{
    try{
      const value:unknown=JSON.parse(rowsText);
      if(!Array.isArray(value))throw new Error("Rows must be a JSON array.");
      return {rows:value as MeasuredDatum[],error:null};
    }catch(e){return {rows:[] as MeasuredDatum[],error:errorMessage(e)};}
  },[rowsText]);
  const tableDraft=():MeasuredTable=>({
    schema:tableSchema,id:stored?.id??draftId,label,version:stored?stored.version+1:1,
    source_asset_id:assetId,source_sha256:currentAsset?.content_sha256??"",
    rows:parsed.rows,
  });
  const [tableError,table]=useMemo(()=>{
    if(parsed.error)return [parsed.error,null] as const;
    try{const next=tableDraft();validateMeasuredTable(next,project);return [null,next] as const;}
    catch(e){return [errorMessage(e),null] as const;}
    // Inputs and source metadata are the only prerequisites.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[parsed,label,stored,draftId,assetId,currentAsset,project.assets]);
  const submitTable=async()=>{
    if(!table || stale || busy)return;
    setError(null);
    try{
      await commit({type:"upsert_measured_table",table});
      // Re-attach to same table on next committed Project.
      setSelectedTable(table.id);setBase(project.revision+1);
    }catch(e){setError(errorMessage(e));}
  };
  const availableTable=stored??tables.find(t=>t.id===selectedTable);
  const chosenData=availableTable?.rows.find(row=>row.key===selectedKey);
  const presentation=():MeasuredView|undefined=>{
    if(view==="bar"){
      if([axisMin,axisMax,maxWidth].some(v=>v.trim()===""))return undefined;
      return {kind:"bar",axis_min_milli:Number(axisMin),axis_max_milli:Number(axisMax),max_width_px:Number(maxWidth)};
    }
    return {kind:view,decimals};
  };
  const bindError=useMemo(()=>{
    if(!scene || !selectedNode || !chosenData)return "Select a scene, metric row and matching Canvas node.";
    const requested=presentation();
    if(!requested)return "Explicit minimum, maximum and pixel width required; axes are never inferred.";
    if(bindings.some(b=>b.scene_id===scene.id && b.node_id===selectedNode))
      return "This Canvas node already has a measured binding. Detach it before attaching another.";
    try{measuredOutput(chosenData,requested);return null;}
    catch(e){return errorMessage(e);}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[scene,selectedNode,chosenData,view,decimals,axisMin,axisMax,maxWidth,bindings]);
  const submitBinding=async()=>{
    if(!scene || !availableTable || !chosenData || !selectedNode || bindError || stale || busy)return;
    const chosenView=presentation();
    if(!chosenView)return;
    setError(null);
    try{
      await commit({type:"upsert_measured_binding",binding_id:crypto.randomUUID(),scene_id:scene.id,
        node_id:selectedNode,table_id:availableTable.id,datum_key:chosenData.key,view:chosenView});
      setBase(project.revision+1);setSelectedNode("");
    }catch(e){setError(errorMessage(e));}
  };
  const detach=async(id:string)=>{
    if(stale||busy)return;
    setError(null);
    try{await commit({type:"detach_measured_binding",binding_id:id});setBase(project.revision+1);}
    catch(e){setError(errorMessage(e));}
  };
  return <section className="production-plan" aria-label="Measured data and units">
    <div className="production-section-title">
      <div><h2>Measured data / source-bound graphics</h2>
        <p>Reference a real imported CSV/JSON asset, author typed units, then bind an explicit value to Canvas.</p></div>
    </div>
    <p className="production-help">This editor cannot independently certify the source file's measurements.
      It checks the asset SHA-256 identity and stores a versioned, explicitly declared table snapshot.
      Validate source rights, units and measurement methodology before using data as evidence.</p>
    {stale && <div className="production-notice" role="status">
      <strong>Data draft is from revision {base}; project is now {project.revision}.</strong>
      <span>Reload to reconcile; the editor never silently overwrites the current project.</span>
      <button className="secondary-button" onClick={()=>stored?load():reset()}>
        <RefreshCw size={14}/> Reload measured draft</button>
    </div>}
    {!assets.length && <p role="status">No content-addressed JSON/CSV asset is imported.
      Import a source in Assets before binding metrics; Motionwright will not invent sample values.</p>}
    <div className="production-section-title"><h3>1. Source table</h3></div>
    <div className="production-plan-grid">
      <label className="production-field"><span>Table to update</span>
        <select aria-label="Measured table" value={selectedTable} onChange={e=>{
          const id=e.target.value;setSelectedTable(id);
          const found=tables.find(t=>t.id===id);
          setLabel(found?.label??"");setRowsText(JSON.stringify(found?.rows??[],null,2));
          setAssetId(found?.source_asset_id??"");setBase(project.revision);setError(null);setSelectedKey("");
        }}><option value="new">New measured table</option>{tables.map(t=>
          <option key={t.id} value={t.id}>{t.label} · v{t.version}</option>)}</select>
      </label>
      <label className="production-field"><span>Imported source asset</span>
        <select aria-label="Measured source asset" value={assetId} onChange={e=>setAssetId(e.target.value)}>
          <option value="">Choose an imported JSON/CSV asset</option>
          {assets.map(a=><option key={a.id} value={a.id}>{a.name} · {a.content_sha256?.slice(0,12)}…</option>)}
        </select>
      </label>
      <label className="production-field"><span>Dataset label</span>
        <input aria-label="Measured dataset label" value={label} maxLength={160} onChange={e=>setLabel(e.target.value)}
          placeholder="Descriptive name of actual source"/></label>
    </div>
    <label className="production-field"><span>Declared numeric rows (JSON)</span>
      <textarea aria-label="Measured numeric rows JSON" rows={8} value={rowsText}
        onChange={e=>setRowsText(e.target.value)} spellCheck={false}/>
      <small>Each row requires key, label, integer milli and unit (count, percent, seconds, milliseconds or bytes).
        Example structure: one object per row; no example numbers are inserted.</small>
    </label>
    {sourceValid && <p className="mono">SHA-256: {currentAsset!.content_sha256} · Version to save: {stored?stored.version+1:1}</p>}
    {tableError && <p className="production-help" role="status">Source preflight: {tableError}</p>}
    <button className="primary-button" disabled={!table || busy || stale} onClick={submitTable}>
      <Save size={14}/>{stored?"Commit next measured source revision":"Register measured source"}</button>
    <div className="production-section-title"><h3>2. Bind an existing Canvas node</h3></div>
    <p className="production-help">Counters and labels update only text. Bars update only rectangle width;
      the minimum and maximum must come from an explicitly reviewed axis. Styling, positioning,
      colors and measurement units are preserved between data revisions.</p>
    {!scene && <p role="status">Choose a working Motion Canvas scene to bind an existing node.</p>}
    <div className="production-plan-grid">
      <label className="production-field"><span>Typed row</span>
        <select aria-label="Measured datum row" value={selectedKey} onChange={e=>setSelectedKey(e.target.value)}>
          <option value="">Select an imported datum</option>
          {(availableTable?.rows??[]).map(row=><option key={row.key} value={row.key}>
            {row.label} · {row.unit}</option>)}
        </select></label>
      <label className="production-field"><span>Output</span>
        <select aria-label="Measured output kind" value={view} onChange={e=>{
          setView(e.target.value as MeasuredView["kind"]);setSelectedNode("");
        }}>
          <option value="counter">Counter · numeric and unit</option>
          <option value="label">Label · source label and value</option>
          <option value="bar">Bar · explicit min/max width</option>
        </select></label>
      <label className="production-field"><span>Canvas object</span>
        <select aria-label="Measured Canvas node" value={selectedNode} onChange={e=>setSelectedNode(e.target.value)}>
          <option value="">Choose an existing {view==="bar"?"rectangle":"text"} node</option>
          {matchingNodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}
        </select></label>
      {view!=="bar"?<label className="production-field"><span>Decimal places (0–3)</span>
        <input type="number" aria-label="Measured decimals" min={0} max={3} step={1} value={decimals}
          onChange={e=>setDecimals(Number(e.target.value))}/></label>:<>
        <label className="production-field"><span>Explicit axis minimum (milli)</span>
          <input aria-label="Measured axis minimum" inputMode="numeric" value={axisMin} onChange={e=>setAxisMin(e.target.value)}/></label>
        <label className="production-field"><span>Explicit axis maximum (milli)</span>
          <input aria-label="Measured axis maximum" inputMode="numeric" value={axisMax} onChange={e=>setAxisMax(e.target.value)}/></label>
        <label className="production-field"><span>Maximum bar width (px)</span>
          <input aria-label="Measured maximum bar width" inputMode="numeric" value={maxWidth} onChange={e=>setMaxWidth(e.target.value)}/></label>
      </>}
    </div>
    {chosenData && !bindError && <p className="mono">Calculated output: {JSON.stringify(measuredOutput(chosenData,presentation()!))}</p>}
    {bindError && <p className="production-help" role="status">{bindError}</p>}
    {error && <p className="production-error" role="alert">{error}</p>}
    <div className="production-button-row">
      <button className="primary-button" disabled={!!bindError || busy || stale}
        onClick={submitBinding}><Save size={14}/> Bind measured output</button>
      <button className="secondary-button" disabled={!scene} onClick={onOpenCanvas}>
        <ExternalLink size={14}/> Edit Canvas objects</button>
    </div>
    {sceneBindings.length>0 && <div className="production-capsule-list">
      {sceneBindings.map(binding=><article key={binding.id}>
        <header><strong>{binding.datum_key} · {binding.view.kind}</strong>
          <button className="secondary-button" disabled={stale||busy} onClick={()=>detach(binding.id)}>
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
