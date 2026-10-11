import { useEffect, useState } from "react";
import { Save, RefreshCw, ExternalLink, Unlink } from "lucide-react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { defaultProceduralConfig, normalizedProceduralConfig, realizeProceduralField } from "./proceduralField";
import type { ProceduralConfig } from "./proceduralField";

const previewIdentity="00000000-0000-4000-8000-000000000095";
type Props={project:Project;scene:Scene|null;commit:(change:Change)=>Promise<void>;busy:boolean;onOpenCanvas:()=>void};
const dimensions=[
  ["seed","Integer seed",0,4294967295],
  ["count","Number of objects",1,64],
  ["columns","Columns",1,16],
  ["origin_x","Stage X",0,1920],
  ["origin_y","Stage Y",0,1080],
  ["area_width","Field width",1,1920],
  ["area_height","Field height",1,1080],
  ["size","Square size",4,128],
  ["opacity_percent","Opacity (%)",1,100],
  ["reveal_step_frames","Entrance interval (frames; 0 disables)",0,10],
  ["reveal_duration_frames","Entrance fade length (frames)",1,60],
] as const;
export default function ProceduralFieldWorkbench({project,scene,commit,busy,onOpenCanvas}:Props){
  const stored=project.production_design?.procedural_fields?.find(field=>field.scene_id===scene?.id);
  const [draft,setDraft]=useState<ProceduralConfig>(()=>normalizedProceduralConfig(structuredClone(stored?.config??defaultProceduralConfig())));
  const [base,setBase]=useState(project.revision);
  const [nodes,setNodes]=useState<CanvasNode[]>([]);
  const [error,setError]=useState<string|null>(null);
  const [previewError,setPreviewError]=useState<string|null>(null);
  const [previewLoading,setPreviewLoading]=useState(false);
  const [previewTime,setPreviewTime]=useState(2);
  const reload=()=>{
    setDraft(normalizedProceduralConfig(structuredClone(stored?.config??defaultProceduralConfig())));
    setBase(project.revision);setError(null);
  };
  useEffect(()=>{
    setDraft(normalizedProceduralConfig(structuredClone(stored?.config??defaultProceduralConfig())));
    setBase(project.revision);setError(null);
    // Deliberately reset on scene identity, not every revision; never silently lose a draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[scene?.id,project.generation]);
  useEffect(()=>{
    let active=true;setPreviewLoading(true);setPreviewError(null);
    realizeProceduralField(stored?.id??previewIdentity,draft).then(items=>{
      if(active){setNodes(items);setPreviewLoading(false);}
    }).catch(e=>{
      if(active){setNodes([]);setPreviewError(e instanceof Error?e.message:String(e));setPreviewLoading(false);}
    });
    return()=>{active=false;};
  },[draft,stored?.id]);
  const stale=base!==project.revision;
  const previewMax=scene?Math.max(0,seconds(scene.duration)-1/30):6;
  const renderedTime=Math.min(previewTime,previewMax);
  const update=async()=>{
    if(!scene || stale || busy || previewError) return;
    setError(null);
    try{
      await commit({type:"upsert_procedural_field",instance_id:stored?.id??crypto.randomUUID(),scene_id:scene.id,config:draft});
      setBase(project.revision+1);
    }catch(e){setError(e instanceof Error?e.message:String(e));}
  };
  const detach=async()=>{
    if(!stored || stale || busy) return;
    setError(null);
    try{await commit({type:"detach_procedural_field",instance_id:stored.id});setBase(project.revision+1);}
    catch(e){setError(e instanceof Error?e.message:String(e));}
  };
  return <section className="production-plan" aria-label="Procedural field designer">
    <div className="production-section-title">
      <div><h2>Procedural field / repetition</h2><p>Seeded Canvas objects, not a baked texture or remote generative model.</p></div>
    </div>
    <p className="production-help">Every square is an independently editable Canvas node.
      Grid and staggered patterns use fixed geometry; Scatter uses a fully specified 32-bit seeded distribution.
      Up to 64 nodes per field, 16 fields per project and one attached field per scene.</p>
    {!scene && <p role="status">Choose a working scene to author a field.</p>}
    {stale && <div className="production-notice" role="status">
      <strong>This draft uses revision {base}; the project is now {project.revision}.</strong>
      <span>Your pending controls are preserved until you reload.</span>
      <button className="secondary-button" onClick={reload}><RefreshCw size={14}/> Reload current field</button>
    </div>}
    <div className="production-plan-grid">
      <label className="production-field"><span>Distribution</span>
        <select aria-label="Procedural distribution" value={draft.distribution}
          onChange={e=>setDraft(v=>({...v,distribution:e.target.value as ProceduralConfig["distribution"]}))}>
          <option value="grid">Grid / precise rhythm</option>
          <option value="staggered">Staggered / repeated offset</option>
          <option value="scatter">Scatter / seeded displacement</option>
        </select>
      </label>
      <label className="production-field"><span>Fill (#RRGGBB)</span>
        <input aria-label="Procedural fill" value={draft.fill} maxLength={7}
          onChange={e=>setDraft(v=>({...v,fill:e.target.value}))}/>
      </label>
      {dimensions.map(([key,label,min,max])=><label className="production-field" key={key}>
        <span>{label}</span>
        <input aria-label={label} type="number" min={min} max={max} step={1} value={Number.isNaN(draft[key])?"":draft[key]}
          onChange={e=>setDraft(v=>({...v,[key]:e.target.value===""?Number.NaN:Number(e.target.value)}))}/>
      </label>)}
    </div>
    {previewError && <p className="production-error" role="alert">{previewError}</p>}
    {error && <p className="production-error" role="alert">{error}</p>}
    <div className="production-button-row">
      <button className="primary-button" disabled={!scene || busy || stale || previewLoading || !!previewError}
        onClick={update}><Save size={14}/>{stored?"Update procedural field":"Add procedural field"}</button>
      {stored && <button className="secondary-button" disabled={busy || stale}
        onClick={detach}><Unlink size={14}/> Detach generator (keep objects)</button>}
      <button className="secondary-button" disabled={!scene}
        onClick={onOpenCanvas}><ExternalLink size={14}/> Edit individual objects</button>
    </div>
    <p className="production-help">Updating recalculates the requested set and preserves compatible human overrides.
      Shrinking fails closed if it would delete human edits or references.
      Native exports require the normal verified render/evidence path.</p>
    <section className="production-approval" aria-label="Procedural editorial preview">
      <h3>Live editorial study · 1920 × 1080</h3>
      <label className="production-field"><span>Editorial playhead · {renderedTime.toFixed(2)} s</span>
        <input aria-label="Procedural preview playhead" type="range" min={0} max={previewMax}
          step={1/30} value={renderedTime} onChange={e=>setPreviewTime(Number(e.target.value))}/>
      </label>
      {previewLoading?<p role="status">Evaluating bounded procedural preview…</p>
       :previewError?<p role="status">No preview until the field constraints are valid.</p>
       :<CompositionStudy nodes={nodes} width={1920} height={1080} time={renderedTime} background="#0C1723"
          label={`Procedural editorial preview, ${draft.distribution} distribution, ${nodes.length} editable objects`}/>}
      <p className="mono">{draft.count} authored objects · seed {draft.seed} ·
        {stored?` attached field ${stored.id.slice(0,8)}…`:" not yet attached"} · no native render receipt</p>
    </section>
  </section>;
}
