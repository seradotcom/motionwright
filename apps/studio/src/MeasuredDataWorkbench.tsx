import { useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, Save, Unlink } from "lucide-react";
import type { Change, Project, Scene } from "./types";
import { seconds } from "./types";
import SceneCompositionStudy from "./SceneCompositionStudy";
import MeasuredSourcePreview from "./MeasuredSourcePreview";
import { inheritedTextInk } from "./canvasInheritedInk";
import { sameValue } from "./creativeProduction";
import { applyMeasuredBinding, applyMeasuredTable, parseMeasuredSource } from "./measuredData";
import type { MeasuredOutput, MeasuredView } from "./measuredData";
import { discardMeasuredPresentation, editMeasuredPresentation, measuredBindingProposal, measuredPresentation, measuredProofMatches, measuredRaw, measuredTable, selectMeasuredBinding, selectMeasuredTable, workbenchScope } from "./creativeWorkbenchDraft";
import type { MeasuredByteProof, MeasuredPresentationRaw, MeasuredRaw, WorkbenchInput } from "./creativeWorkbenchDraft";
import { useCreativeWorkbenchDraft, WorkbenchDraftFeedback } from "./useCreativeWorkbenchDraft";

type Props={project:Project;scene:Scene|null;busy:boolean;commit:(change:Change)=>Promise<Project>;onSelectScene:(id:string)=>void;onOpenCanvas:()=>void};
const hash=/^[0-9a-f]{64}$/;
const errorMessage=(reason:unknown)=>reason instanceof Error?reason.message:String(reason);
const outputLabel=(output:MeasuredOutput)=>output.kind==="text"?output.value:output.width_px+" px wide";
export default function MeasuredDataWorkbench(props:Props){
  return <MeasuredEditor key={workbenchScope(props.project,{kind:"measured",sceneId:props.scene?.id??null})} {...props}/>;
}
/** Readonly source preview remains derived from locally reselected exact bytes.
 * Preserved rows are editable-work custody, never recovered byte verification. */
function MeasuredEditor({project,scene,busy,commit,onSelectScene,onOpenCanvas}:Props){
  const editor=useCreativeWorkbenchDraft(project,{kind:"measured",sceneId:scene?.id??null},commit,busy);
  const input=editor.draft.input as Extract<WorkbenchInput,{kind:"measured"}>, raw=measuredRaw(input);
  const {label,assetId,rowsText}=raw;
  const {selectedNode,selectedKey,view,decimals,axisMin,axisMax,maxWidth}=measuredPresentation(input);
  const selectedTable=input.selectedTable, selectedBinding=input.selectedBinding??"";
  const patch=(next:Partial<MeasuredRaw>)=>editor.edit({...input,tables:{...input.tables,[selectedTable]:{...raw,...next}}});
  const patchPresentation=(next:Partial<MeasuredPresentationRaw>)=>editor.edit(editMeasuredPresentation(input,next));
  const [previewBindingId]=useState(()=>crypto.randomUUID());
  const [previewTime,setPreviewTime]=useState(0);
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
  const [previewSceneId, setPreviewSceneId] = useState("");
  const [revisionError, revisionPreview] = useMemo(() => {
    if (!sourceBytesMatch || !stored || !table) return [null, null] as const;
    try {
      const candidate = structuredClone(project);
      applyMeasuredTable(candidate, table);
      const impact = candidate.production_design!.last_measured_impact!;
      return [null, {impact,
        affected: candidate.scenes.filter(item => impact.scene_ids.includes(item.id))}] as const;
    } catch (reason) {
      return [errorMessage(reason), null] as const;
    }
  }, [project, sourceBytesMatch, stored, table]);
  const proposedScene = revisionPreview?.affected.find(item => item.id === previewSceneId)
    ?? revisionPreview?.affected.find(item => item.id === scene?.id)
    ?? revisionPreview?.affected[0];
  const savedScene = proposedScene && project.scenes.find(item => item.id === proposedScene.id);
  const boundTextNodes = useMemo(() => new Set(bindings
    .filter(binding => binding.table_id === table?.id && binding.scene_id === proposedScene?.id && binding.view.kind !== "bar")
    .map(binding => binding.node_id)), [bindings, proposedScene, table?.id]);
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
  const sceneBindings=bindings.filter(binding=>binding.scene_id===scene?.id);
  const availableTable=tables.find(table=>table.id===(selectedBinding?input.bindings![selectedBinding].tableId:raw.draftId));
  const chosenData=availableTable?.rows.find(row=>row.key===selectedKey);
  const currentBinding=sceneBindings.find(binding=>binding.id===selectedBinding);
  const currentNode=scene?.nodes.find(node=>node.id===selectedNode);
  const [bindError,bindingStudy]=useMemo(()=>{
    try{
      if(!scene||!selectedNode||!chosenData)throw new Error("Select a scene, metric row and matching Canvas node.");
      const change=measuredBindingProposal(input,project,scene.id,previewBindingId),candidate=structuredClone(project);
      applyMeasuredBinding(candidate,change);
      const proposed=candidate.production_design!.measured_bindings!.find(binding=>binding.id===change.binding_id)!;
      const before=project.production_design?.measured_bindings?.find(binding=>binding.id===change.binding_id);
      return [null,{scene:candidate.scenes.find(item=>item.id===scene.id)!,binding:proposed,
        changed:!before||!sameValue(before.view,proposed.view)||before.datum_key!==proposed.datum_key}] as const;
    }catch(reason){return [errorMessage(reason),null] as const;}
  },[scene,selectedNode,chosenData,input,project,previewBindingId]);
  const selectOutput=(id:string)=>{
    try{
      const next=selectMeasuredBinding(input,id,project,scene?.id??null);
      if(next.selectedTable!==input.selectedTable){setVerified(null);fileEpoch.current++;}
      editor.edit(next);setFileError(null);
    }catch(reason){setFileError(errorMessage(reason));}
  };
  const previewMax=scene?Math.max(0,seconds(scene.duration)-1/30):0, renderedTime=Math.min(previewTime,previewMax);
  const submitTable=async()=>{if(table&&sourceBytesMatch&&!revisionError&&!editor.blocked)await editor.send("table",verified);};
  const submitBinding=async()=>{if(bindingStudy?.changed&&!bindError&&!editor.blocked)await editor.send("binding");};
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
    {revisionError && <p className="production-error" role="alert">Source revision cannot be applied: {revisionError}</p>}
    {revisionPreview && <section className="production-approval" aria-label="Measured source revision preview">
      <h3>Source revision preview</h3>
      <p>Version {stored?.version} → {table?.version} · {revisionPreview.affected.length} affected scenes ·{" "}
        {revisionPreview.impact.deliverable_ids.length} affected output profiles</p>
      {revisionPreview.affected.length > 1 && <label className="production-field">
        <span>Affected scene</span>
        <select aria-label="Measured revision preview scene" value={proposedScene?.id ?? ""}
          onChange={event => setPreviewSceneId(event.target.value)}>
          {revisionPreview.affected.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
      {savedScene && proposedScene && <MeasuredSourcePreview
        key={JSON.stringify([project.revision, proposedScene.id, table?.source_sha256])}
        before={savedScene} after={proposedScene} boundTextNodes={boundTextNodes}
        frameRate={project.deliverables[0]?.frame_rate} cameraReframes={project.production_design?.camera_reframes}
        ink={inheritedTextInk(project.visual_language.palette)}
        time={Math.min(renderedTime, Math.max(0, seconds(proposedScene.duration) - 1/30))}
        openCanvas={() => { onSelectScene(proposedScene.id); onOpenCanvas(); }}/>}
    </section>}
    <button className="primary-button" disabled={!table || !sourceBytesMatch || !!revisionError || editor.blocked} onClick={submitTable}>
      <Save size={14}/>{stored?"Commit next measured source revision":"Register measured source"}</button>
    <div className="production-section-title"><h3>2. Create or edit a measured output</h3></div>
    <p className="production-help">Counters and labels update only text. Bars update only rectangle width;
      the minimum and maximum must come from an explicitly reviewed axis. Styling, positioning,
      colors and measurement units are preserved between data revisions. Existing outputs keep their binding, source table and Canvas node identities.</p>
    <label className="production-field"><span>Output to edit</span>
      <select aria-label="Measured output to edit" value={selectedBinding} onChange={event=>selectOutput(event.target.value)}>
        <option value="">Bind a new output</option>
        {sceneBindings.map(binding=><option key={binding.id} value={binding.id}>
          {scene?.nodes.find(node=>node.id===binding.node_id)?.name??"Missing object"} · {binding.datum_key} · {binding.view.kind}
        </option>)}
        {Object.keys(input.bindings??{}).filter(id=>!sceneBindings.some(binding=>binding.id===id)).map(id=>
          <option key={id} value={id}>Retained draft · binding removed · {id.slice(0,8)}</option>)}
      </select>
    </label>
    {selectedBinding&&<p className="production-help" role="status">
      Editing the original binding · source {availableTable?.label??"unavailable"} · version {availableTable?.version??"unavailable"}.
      Other output and source drafts remain retained when you switch.
    </p>}
    {!scene && <p role="status">Choose a working Motion Canvas scene to bind an existing node.</p>}
    <div className="production-plan-grid">
      <label className="production-field"><span>Typed row</span>
        <select aria-label="Measured datum row" value={selectedKey} onChange={e=>patchPresentation({selectedKey:e.target.value})}>
          <option value="">Select an imported datum</option>
          {(availableTable?.rows??[]).map(row=><option key={row.key} value={row.key}>
            {row.label} · {row.unit}</option>)}
        </select></label>
      <label className="production-field"><span>Output</span>
        <select aria-label="Measured output kind" value={view} onChange={e=>{
          patchPresentation({view:e.target.value as MeasuredView["kind"]});
        }}>
          <option value="counter" disabled={!!selectedBinding&&currentNode?.kind!=="text"}>Counter · numeric and unit</option>
          <option value="label" disabled={!!selectedBinding&&currentNode?.kind!=="text"}>Label · source label and value</option>
          <option value="bar" disabled={!!selectedBinding&&currentNode?.kind!=="rectangle"}>Bar · explicit min/max width</option>
        </select></label>
      <label className="production-field"><span>Canvas object</span>
        <select aria-label="Measured Canvas node" value={selectedNode} disabled={!!selectedBinding} onChange={e=>patchPresentation({selectedNode:e.target.value})}>
          <option value="">Choose an existing {view==="bar"?"rectangle":"text"} node</option>
          {selectedNode&&!matchingNodes.some(node=>node.id===selectedNode)&&<option value={selectedNode}>Retained node selection · choose a matching object</option>}
          {matchingNodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}
        </select></label>
      {view!=="bar"?<label className="production-field"><span>Decimal places (0–3)</span>
        <input type="text" inputMode="numeric" aria-label="Measured decimals" value={decimals}
          onChange={e=>patchPresentation({decimals:e.target.value})}/></label>:<>
        <label className="production-field"><span>Explicit axis minimum (milli)</span>
          <input aria-label="Measured axis minimum" inputMode="numeric" value={axisMin} onChange={e=>patchPresentation({axisMin:e.target.value})}/></label>
        <label className="production-field"><span>Explicit axis maximum (milli)</span>
          <input aria-label="Measured axis maximum" inputMode="numeric" value={axisMax} onChange={e=>patchPresentation({axisMax:e.target.value})}/></label>
        <label className="production-field"><span>Maximum bar width (px)</span>
          <input aria-label="Measured maximum bar width" inputMode="numeric" value={maxWidth} onChange={e=>patchPresentation({maxWidth:e.target.value})}/></label>
      </>}
    </div>
    {chosenData&&<p className="production-help">Source: {chosenData.label} · {chosenData.milli/1000} {chosenData.unit}.
      {view==="bar"&&" The explicit axis is in thousandths of that same unit; no conversion or inferred range is applied."}</p>}
    {bindingStudy&&<div className="production-plan-grid" aria-label="Measured output comparison">
      <div><strong>Saved output</strong><p className="mono">{currentBinding?outputLabel(currentBinding.baseline):
        currentNode?.kind==="text"?currentNode.text:currentNode?currentNode.width+" px":"No object selected"}</p></div>
      <div><strong>Proposed output</strong><p className="mono">{outputLabel(bindingStudy.binding.baseline)}</p></div>
    </div>}
    {bindError && <p className="production-help" role="status">{bindError}</p>}
    {bindingStudy&&!bindingStudy.changed&&<p className="production-help" role="status">This output matches its saved source and presentation.</p>}
    <div className="production-button-row">
      <button className="primary-button" disabled={!!bindError || !bindingStudy?.changed || editor.blocked}
        onClick={submitBinding}><Save size={14}/> {selectedBinding?"Update measured output":"Bind measured output"}</button>
      <button className="secondary-button" disabled={editor.blocked}
        onClick={()=>editor.edit(discardMeasuredPresentation(input,project,scene?.id??null))}>
        {selectedBinding?(currentBinding?"Use saved output":"Discard removed output draft"):"Clear new output draft"}
      </button>
      <button className="secondary-button" disabled={!scene} onClick={onOpenCanvas}>
        <ExternalLink size={14}/> Edit Canvas objects</button>
    </div>
    {scene&&bindingStudy&&<section className="production-approval" aria-label="Measured output editorial study">
      <h3>Before and after · editorial study</h3>
      <label className="production-field"><span>Preview time · {renderedTime.toFixed(2)} s</span>
        <input aria-label="Measured preview playhead" type="range" min={0} max={previewMax} step={1/30}
          value={renderedTime} onChange={event=>setPreviewTime(Number(event.target.value))}/></label>
      <div className="production-plan-grid">
        <SceneCompositionStudy scene={scene} frameRate={project.deliverables[0]?.frame_rate} cameraReframes={project.production_design?.camera_reframes} time={renderedTime} background="#0C1723" ink={inheritedTextInk(project.visual_language.palette)}
          label="Measured output before · saved Canvas"/>
        <SceneCompositionStudy scene={bindingStudy.scene} frameRate={project.deliverables[0]?.frame_rate} cameraReframes={project.production_design?.camera_reframes} time={renderedTime} background="#0C1723" ink={inheritedTextInk(project.visual_language.palette)}
          label="Measured output after · proposed Canvas"/>
      </div>
      <p className="production-help">Source geometry and styling at the same scene time. This editorial study is not a Native rendering or a measurement certificate.</p>
    </section>}
    {sceneBindings.length>0 && <div className="production-capsule-list">
      {sceneBindings.map(binding=><article key={binding.id} aria-label={"Measured binding "+binding.id}>
        <header><strong>{scene?.nodes.find(node=>node.id===binding.node_id)?.name} · {binding.datum_key} · {binding.view.kind}</strong>
          <div className="production-button-row">
            <button className="secondary-button" onClick={()=>selectOutput(binding.id)}>Edit output</button>
            <button className="secondary-button" disabled={editor.blocked} onClick={()=>detach(binding.id)}>
              <Unlink size={14}/> Detach (keep Canvas)</button>
          </div></header>
        <p className="mono">{outputLabel(binding.baseline)} · source {tables.find(table=>table.id===binding.table_id)?.label??"unavailable"}</p>
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
