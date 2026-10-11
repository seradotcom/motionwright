import { previewCreativePatchUndo } from "./creativeUndo";
import { nativeRepairDraftIsCurrent } from "./nativeReviewRepair";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import { useEffect, useMemo, useState } from "react";
import { Check, Eye, Plus, Trash2 } from "lucide-react";
import { previewCreativePatch } from "./api";
import CompositionStudy from "./CompositionStudy";
import type { CreativePatch, ScopedCanvasEdit } from "./creativeProduction";
import type { CanvasNode, CanvasTransform, Change, Project, Scene } from "./types";
import { seconds } from "./types";

type Preview = Awaited<ReturnType<typeof previewCreativePatch>>;
export default function CreativePatchWorkbench({project,scene,commit,busy,playhead,onSeek,repairDraft,onDismissRepair}: {
  project:Project; scene:Scene|null; commit:(change:Change)=>Promise<void>; busy:boolean;
  playhead:number; onSeek:(time:number)=>void;
  repairDraft:NativeReviewRepairDraft|null; onDismissRepair:()=>void;
}) {
  const [nodeId,setNodeId]=useState(scene?.nodes[0]?.id ?? "");
  const [kind,setKind]=useState<"text"|"transform">("text");
  const [text,setText]=useState("");
  const [transform,setTransform]=useState<CanvasTransform>({x:0,y:0,width:0,height:0,rotation_deg:0,opacity:1});
  const [rationale,setRationale]=useState("");
  const [edits,setEdits]=useState<ScopedCanvasEdit[]>([]);
  const [base,setBase]=useState(project.revision);
  const [preview,setPreview]=useState<Preview|null>(null);
  const [undoTarget,setUndoTarget]=useState<string|null>(null);
  const [pending,setPending]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const node=scene?.nodes.find(n=>n.id===nodeId);
  useEffect(()=>{
    setEdits([]);setPreview(null);setUndoTarget(null);setError(null);setBase(project.revision);
    setNodeId(scene?.nodes[0]?.id ?? "");
    // A selection change starts a new explicitly scoped draft; an unrelated revision does not erase it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[scene?.id,project.generation]);
  useEffect(()=>{
    if (!repairDraft) return;
    if (!nativeRepairDraftIsCurrent(project,scene,repairDraft)) {
      setError("The native frame repair source is stale, missing or from another scene. Re-inspect current native frames.");
      return;
    }
    setEdits([]);setPreview(null);setUndoTarget(null);setError(null);
    setBase(repairDraft.revision);setNodeId(repairDraft.targetNodeId);
    setKind(repairDraft.editKind);setRationale(repairDraft.rationale);
    // This deliberately does not fill replacement values from the review note.
    // The operator must author a change and run a separate preview/commit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[repairDraft,project.id,project.generation,scene?.id]);
  useEffect(()=>{
    if (!node) return;
    setText(node.text ?? "");
    setTransform({x:node.x,y:node.y,width:node.width,height:node.height,rotation_deg:node.rotation_deg,opacity:node.opacity});
  },[node]);
  const sourceStale=repairDraft!==null && !nativeRepairDraftIsCurrent(project,scene,repairDraft);
  const stale=base!==project.revision || sourceStale;
  const background=project.visual_language.palette.find(t=>["surface","background"].includes(t.name.toLowerCase()))?.value ?? "#0F1216";
  const time=scene?Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))):0;
  const resultNodes=useMemo(()=>{
    if (!scene || !preview) return scene?.nodes ?? [];
    const updated=new Map(preview.after.map(n=>[n.id,n]));
    return scene.nodes.map(n=>updated.get(n.id) ?? n);
  },[scene,preview]);
  const patch:CreativePatch={scene_id:scene?.id ?? "",base_revision:base,rationale,edits};
  const sourceUnchanged=repairDraft!==null && !!node && (kind==="text" ? text===(node.text??"") :
    Object.entries(transform).every(([key,value])=>node[key as keyof CanvasNode]===value));
  const queue=()=>{
    if (!node || stale || sourceUnchanged || (repairDraft!==null && node.id!==repairDraft.targetNodeId)) return;
    const edit:ScopedCanvasEdit=kind==="text"?{kind:"text",node_id:node.id,text}:{kind:"transform",node_id:node.id,transform};
    setEdits([...edits.filter(e=>e.node_id!==edit.node_id || e.kind!==edit.kind),edit]);
    setPreview(null);setUndoTarget(null);setError(null);
  };
  const inspect=async()=>{
    setPending(true);setError(null);setPreview(null);setUndoTarget(null);
    try { setPreview(await previewCreativePatch(project,patch)); }
    catch(reason){setError(reason instanceof Error?reason.message:String(reason));}
    finally {setPending(false);}
  };
  const reset=()=>{setUndoTarget(null);setBase(project.revision);setEdits([]);setPreview(null);setRationale("");setError(null);onDismissRepair();};
  const records=(project.production_design?.patches ?? []).filter(record=>record.scene_id===scene?.id);
  const inspectUndo=(id:string)=>{
    setError(null);setPreview(null);setEdits([]);setBase(project.revision);setUndoTarget(null);
    try{const result=previewCreativePatchUndo(project,id);setPreview(result);setUndoTarget(id);}
    catch(reason){setError(reason instanceof Error?reason.message:String(reason));}
  };
  const editSummary=(edit:ScopedCanvasEdit)=>{
    const label=scene?.nodes.find(n=>n.id===edit.node_id)?.name ?? edit.node_id;
    return `${label} / ${edit.kind}`;
  };
  return <div className="production-patch">
    <div className="production-section-title"><Eye size={18}/><div><h2>Scoped changes</h2><p>Stage a bounded proposal, inspect the difference and commit once.</p></div></div>
    {repairDraft && <div className="production-notice" role="status"><strong>Native frame {repairDraft.frameIndex} · source r{repairDraft.revision} · uncommitted repair</strong><span>PNG SHA-256 {repairDraft.frameSha256}. This handoff is a manual observation, not source authenticity or a quality PASS. The target object is fixed for this draft; discard it to select another. Enter a replacement, preview the scoped diff, then explicitly commit.</span></div>}
    <div className="production-notice"><strong>Revision {base} · one scene · {edits.length} proposed edit{edits.length===1?"":"s"}</strong>
      <span>Scene and property locks are checked in preview and again by the revisioned service at commit. This view is an editorial comparison, not renderer verification.</span></div>
    {stale && <div className="production-error" role="alert">The project changed to revision {project.revision}. This proposal cannot commit against an old base. <button className="secondary-button" onClick={reset}>Start a new scoped draft</button></div>}
    <div className="production-plan-grid">
      <section className="production-patch-editor" aria-label="Scoped proposal editor">
        <label className="production-field"><span>Target object</span><select aria-label="Patch target object" value={nodeId} disabled={repairDraft!==null} onChange={e=>setNodeId(e.target.value)}><option value="" disabled>Select an object</option>{scene?.nodes.map(n=><option key={n.id} value={n.id}>{n.name}</option>)}</select></label>
        <label className="production-field"><span>Edit</span><select aria-label="Patch edit kind" value={kind} onChange={e=>setKind(e.target.value as "text"|"transform")}><option value="text">Text only</option><option value="transform">Transform only</option></select></label>
        {kind==="text"?<label className="production-field"><span>Replacement text</span><textarea aria-label="Patch replacement text" rows={4} value={text} onChange={e=>setText(e.target.value)}/></label>
          :<div className="production-transform-grid">{(["x","y","width","height","rotation_deg","opacity"] as const).map(field=><label className="production-field" key={field}><span>{field.replace("_deg"," (deg)")}</span><input type="number" step={field==="opacity"?.01:1} value={transform[field]} onChange={e=>setTransform({...transform,[field]:Number(e.target.value)})}/></label>)}</div>}
        <button className="secondary-button" disabled={!node || stale || sourceUnchanged || (repairDraft!==null && node.id!==repairDraft.targetNodeId) || edits.length>=128 || pending} onClick={queue}><Plus size={14}/> Add to proposal</button>
        <label className="production-field"><span>Visible reason for this change</span><textarea aria-label="Patch rationale" rows={3} placeholder="What is wrong, where is it visible, and why does this edit help?" value={rationale} onChange={e=>{setRationale(e.target.value);setPreview(null);}}/></label>
      </section>
      <section className="production-patch-queue" aria-label="Proposed edits"><h3>Proposed edits</h3>{edits.length===0?<p className="production-help">No edits queued. Selecting or typing does not change the project.</p>:edits.map((edit,index)=><div key={edit.node_id+edit.kind}><span>{editSummary(edit)}</span><button className="icon-button" aria-label={"Remove proposed edit "+(index+1)} onClick={()=>{setEdits(edits.filter((_,i)=>i!==index));setPreview(null);}}><Trash2 size={14}/></button></div>)}
        <button className="primary-button" disabled={!scene || !edits.length || !rationale.trim() || stale || pending || busy} onClick={inspect}><Eye size={14}/>{pending?"Inspecting proposal…":"Preview scoped changes"}</button>
        {error && <p className="production-error" role="alert">{error}</p>}
        {preview && <div className="production-patch-receipt"><strong>Editorial diff prepared</strong><p>{preview.before.length} object{preview.before.length===1?"":"s"} · full selected scene invalidated: {seconds(preview.dirty_start).toFixed(3)}–{seconds(preview.dirty_end).toFixed(3)} s.</p><p>Dependencies outside this scene are not certified by this diff. Canonical render/effect checks still run in the production path.</p></div>}
      </section>
    </div>
    {scene && <><div className="production-compare-grid"><figure><figcaption>BEFORE · current project</figcaption><CompositionStudy nodes={scene.nodes} time={time} background={background} label="Before scoped changes"/></figure><figure><figcaption>AFTER · uncommitted proposal</figcaption><CompositionStudy nodes={resultNodes} time={time} background={background} label="After scoped changes"/></figure></div>
      <div className="production-transport"><span className="mono">{time.toFixed(3)} s</span><input aria-label="Comparison playhead" type="range" min={0} max={seconds(scene.duration)} step={1/30} value={time} onChange={e=>onSeek(seconds(scene.start)+Number(e.target.value))}/></div>
      <p className="production-help">Both sides share the application playhead. This bounded study draws flat text, shapes and circles; parenting, imported media and advanced paint effects must be reviewed in native frames.</p></>}
    <div className="production-button-row"><button className="primary-button" disabled={!preview || stale || busy || pending} onClick={()=>commit(undoTarget?{type:"undo_creative_patch",patch_id:undoTarget}:{type:"apply_creative_patch",patch})}><Check size={14}/> {undoTarget?"Apply undo as new revision":"Apply as one revision"}</button><button className="secondary-button" disabled={busy || pending} onClick={reset}>Discard proposal</button></div>
    <section className="production-undo-history" aria-label="Reversible scoped changes"><h3>Reversible scoped changes</h3>
      <p className="production-help">The latest 64 scoped edits are available here, within a four-megabyte window. Undo creates a new revision and rejects conflicts or locked properties. The full application change journal is not rewound or deleted.</p>
      {records.length===0?<p className="production-help">No applied scoped changes in this scene yet.</p>:[...records].reverse().map(record=>{
        const reverted=records.some(other=>other.reverts===record.id);
        return <article key={record.id}><div><strong>{record.rationale}</strong><span>Base r{record.source_revision} · {record.before.length} object{record.before.length===1?"":"s"}{reverted?" · inverse recorded":""}</span></div>
          <button className="secondary-button" disabled={busy || pending || reverted} onClick={()=>inspectUndo(record.id)}>{record.reverts?"Preview redo":"Preview undo"}</button></article>;
      })}
    </section>
    {preview && <details className="production-diff-details"><summary>Exact changed object values</summary><pre>{JSON.stringify(preview.after.map((after,index)=>({object:after.name,before:preview.before[index] as CanvasNode,after})),null,2)}</pre></details>}
  </div>;
}
