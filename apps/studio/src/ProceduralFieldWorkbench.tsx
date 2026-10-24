import { useEffect, useState } from "react";
import { Save, ExternalLink, Unlink } from "lucide-react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import { applyProceduralChange, defaultProceduralConfig } from "./proceduralField";
import { defaultProceduralInfluenceRaw } from "./proceduralInfluence";
import ProceduralInfluenceControls from "./ProceduralInfluenceControls";
import type { ProceduralConfig } from "./proceduralField";
import { proceduralConfig, workbenchScope } from "./creativeWorkbenchDraft";
import type { ProceduralRaw, WorkbenchInput } from "./creativeWorkbenchDraft";
import { useCreativeWorkbenchDraft, WorkbenchDraftFeedback } from "./useCreativeWorkbenchDraft";

const previewIdentity="00000000-0000-4000-8000-000000000095";
type Props={project:Project;scene:Scene|null;displayProfile?:Project["deliverables"][number]|null;commit:(change:Change)=>Promise<Project>;busy:boolean;onOpenCanvas:()=>void};
const dimensions=[
  ["seed","Integer seed",0,4294967295],
  ["count","Number of objects",1,64],
  ["columns","Columns",1,16],
  ["origin_x","Stage X",0,1920],
  ["origin_y","Stage Y",0,1080],
  ["area_width","Field width",1,1920],
  ["area_height","Field height",1,1080],
  ["size","Object size",4,128],
  ["opacity_percent","Opacity (%) · Native Film requires 100",1,100],
  ["reveal_step_frames","Entrance interval (frames; 0 disables)",0,10],
  ["reveal_duration_frames","Entrance fade length (frames)",1,60],
] as const;
export default function ProceduralFieldWorkbench(props: Props) {
  return <ProceduralEditor key={workbenchScope(props.project,{kind:"procedural",sceneId:props.scene?.id??null})} {...props}/>;
}
function ProceduralEditor({project,scene,commit,busy,onOpenCanvas,displayProfile}:Props){
  const stored=project.production_design?.procedural_fields?.find(field=>field.scene_id===scene?.id);
  const editor=useCreativeWorkbenchDraft(project,{kind:"procedural",sceneId:scene?.id??null},commit,busy);
  const input=editor.draft.input as Extract<WorkbenchInput,{kind:"procedural"}>, draft=input.config;
  const setDraft=(update:(value:ProceduralRaw)=>ProceduralRaw)=>editor.edit({...input,config:update(draft)});
  const [nodes,setNodes]=useState<CanvasNode[]>([]);
  const [previewError,setPreviewError]=useState<string|null>(null);
  const [previewLoading,setPreviewLoading]=useState(false);
  const [previewTime,setPreviewTime]=useState(2);
  const [reviewMode,setReviewMode]=useState<"saved"|"draft">("draft");
  let influenceDefaults=defaultProceduralInfluenceRaw(stored?.config??defaultProceduralConfig());
  try {
    const {influence:ignored,...base}=draft; void ignored;
    influenceDefaults=defaultProceduralInfluenceRaw(proceduralConfig(base));
  } catch { /* Invalid base text remains visible and is reported by preparation. */ }
  useEffect(()=>{
    let active=true;setPreviewLoading(true);setPreviewError(null);
    Promise.resolve().then(async()=>{
      if(!scene) throw new Error("Choose a working scene to review this field.");
      const candidate=structuredClone(project);
      await applyProceduralChange(candidate,{type:"upsert_procedural_field",instance_id:stored?.id??previewIdentity,
        scene_id:scene.id,config:proceduralConfig(draft)});
      return candidate.scenes.find(value=>value.id===scene.id)!.nodes;
    }).then(items=>{
      if(active){setNodes(items);setPreviewLoading(false);}
    }).catch(e=>{
      if(active){setNodes([]);setPreviewError(e instanceof Error?e.message:String(e));setPreviewLoading(false);}
    });
    return()=>{active=false;};
  },[draft,project,scene,stored?.id]);
  const visibleNodes=reviewMode==="saved"?(scene?.nodes??[]):nodes;
  const previewMax=scene?Math.max(0,seconds(scene.duration)-1/30):6;
  const renderedTime=Math.min(previewTime,previewMax);
  const update=async()=>{ if(scene&&!editor.blocked&&!previewError&&!previewLoading) await editor.send("save"); };
  const detach=async()=>{ if(stored&&!editor.blocked) await editor.send("detach"); };
  return <section className="production-plan" aria-label="Procedural field designer">
    <div className="production-section-title">
      <div><h2>Procedural field / repetition</h2><p>Seeded Canvas objects, not a baked texture or remote generative model.</p></div>
    </div>
    <p className="production-help">Every object is a separately editable Canvas square or circle.
      Grid/staggered, seeded scatter, fixed-point radial and progressive spiral layouts share stable IDs.
      Up to 64 nodes per field, 16 fields per project and one attached field per scene.</p>
    {!scene && <p role="status">Choose a working scene to author a field.</p>}
    <WorkbenchDraftFeedback editor={editor} project={project}/>
    <div className="production-plan-grid">
      <label className="production-field"><span>Distribution</span>
        <select aria-label="Procedural distribution" value={draft.distribution}
          onChange={e=>setDraft(v=>({...v,distribution:e.target.value as ProceduralConfig["distribution"]}))}>
          <option value="grid">Grid / precise rhythm</option>
          <option value="staggered">Staggered / repeated offset</option>
          <option value="scatter">Scatter / seeded displacement</option>
          <option value="radial">Radial / elliptic orbit</option>
          <option value="spiral">Spiral / progressive orbit</option>
        </select>
      </label>
      <label className="production-field"><span>Primitive</span>
        <select aria-label="Procedural primitive" value={draft.shape??"square"}
          onChange={e=>setDraft(v=>({...v,shape:e.target.value as NonNullable<ProceduralConfig["shape"]>}))}>
          <option value="square">Square</option>
          <option value="circle">Circle</option>
        </select>
      </label>
      <label className="production-field"><span>Fill (#RRGGBB)</span>
        <input aria-label="Procedural fill" value={draft.fill} maxLength={7}
          onChange={e=>setDraft(v=>({...v,fill:e.target.value}))}/>
      </label>
      {dimensions.map(([key,label])=><label className="production-field" key={key}>
        <span>{label}</span>
        <input aria-label={label} type="text" inputMode="numeric" value={draft[key]}
          onChange={e=>setDraft(v=>({...v,[key]:e.target.value}))}/>
      </label>)}
    </div>
    <ProceduralInfluenceControls value={draft.influence} defaults={influenceDefaults}
      onChange={influence=>setDraft(value=>({...value,influence}))}/>
    {previewError && <p className="production-error" role="alert">{previewError}</p>}
    <div className="production-button-row">
      <button className="primary-button" disabled={!scene || editor.blocked || previewLoading || !!previewError}
        onClick={update}><Save size={14}/>{stored?"Update procedural field":"Add procedural field"}</button>
      {stored && <button className="secondary-button" disabled={editor.blocked}
        onClick={detach}><Unlink size={14}/> Detach generator (keep objects)</button>}
      <button className="secondary-button" disabled={!scene}
        onClick={onOpenCanvas}><ExternalLink size={14}/> Edit individual objects</button>
    </div>
    <p className="production-help">Updating recalculates the requested set and preserves compatible human overrides.
      Shrinking fails closed if it would delete human edits or references.
      Native exports require the normal verified render/evidence path.</p>
    <section className="production-approval" aria-label="Procedural editorial preview">
      <h3>Live editorial study · 1920 × 1080</h3>
      <div className="production-button-row" role="group" aria-label="Procedural preview source">
        <button className="secondary-button" aria-pressed={reviewMode==="saved"} onClick={()=>setReviewMode("saved")}>Saved</button>
        <button className="secondary-button" aria-pressed={reviewMode==="draft"} onClick={()=>setReviewMode("draft")}>Draft</button>
      </div>
      <p className="production-help">{reviewMode==="saved"?"Saved project objects, including individual edits.":"Current draft applied to the saved objects, preserving compatible individual edits."}</p>
      <label className="production-field"><span>Editorial playhead · {renderedTime.toFixed(2)} s</span>
        <input aria-label="Procedural preview playhead" type="range" min={0} max={previewMax}
          step={1/30} value={renderedTime} onChange={e=>setPreviewTime(Number(e.target.value))}/>
      </label>
      {reviewMode==="draft"&&previewLoading?<p role="status">Evaluating bounded procedural preview…</p>
       :reviewMode==="draft"&&previewError?<p role="status">No draft preview until the field constraints are valid. Saved objects remain available.</p>
       :<CompositionStudy nodes={visibleNodes} width={1920} height={1080} time={renderedTime} background="#0C1723" ink={inheritedTextInk(project.visual_language.palette)} textOverrides={displayProfile?.text_overrides}
          label={`Procedural editorial preview, ${reviewMode}, ${visibleNodes.length} editable objects`}/>}
      <p className="mono">{draft.count} authored objects · seed {draft.seed} ·
        {stored?` attached field ${stored.id.slice(0,8)}…`:" not yet attached"} · no native render receipt</p>
    </section>
  </section>;
}
