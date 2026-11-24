import { useEffect, useState } from "react";
import { Save, ExternalLink, Unlink } from "lucide-react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import { realizeProceduralField, validateProceduralAuthoringBounds } from "./proceduralField";
import type { ProceduralConfig } from "./proceduralField";
import { proceduralConfig, rawProceduralInfluence, workbenchScope } from "./creativeWorkbenchDraft";
import type { InfluenceRaw, ProceduralRaw, WorkbenchInput } from "./creativeWorkbenchDraft";
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
const influenceDimensions = [
  ["center_x", "Influence center X"], ["center_y", "Influence center Y"], ["radius", "Influence radius (px)"],
  ["offset_x", "Influence offset X"], ["offset_y", "Influence offset Y"], ["scale_percent", "Influence center scale (%)"],
  ["rotation_deg", "Influence static orientation (degrees)"],
] as const;
export default function ProceduralFieldWorkbench(props: Props) {
  return <ProceduralEditor key={workbenchScope(props.project,{kind:"procedural",sceneId:props.scene?.id??null})} {...props}/>;
}
function ProceduralEditor({project,scene,commit,busy,onOpenCanvas,displayProfile}:Props){
  const stored=project.production_design?.procedural_fields?.find(field=>field.scene_id===scene?.id);
  const editor=useCreativeWorkbenchDraft(project,{kind:"procedural",sceneId:scene?.id??null},commit,busy);
  const input=editor.draft.input as Extract<WorkbenchInput,{kind:"procedural"}>, draft=input.config;
  const setDraft=(update:(value:ProceduralRaw)=>ProceduralRaw)=>editor.edit({...input,config:update(draft)});
  const influence = draft.influence ?? rawProceduralInfluence();
  const patchInfluence = (patch: Partial<Omit<InfluenceRaw, "enabled">>) =>
    setDraft(value => ({ ...value, influence: { ...(value.influence ?? rawProceduralInfluence()), ...patch } }));
  const [nodes,setNodes]=useState<CanvasNode[]>([]);
  const [previewError,setPreviewError]=useState<string|null>(null);
  const [previewLoading,setPreviewLoading]=useState(false);
  const [previewTime,setPreviewTime]=useState(2);
  useEffect(()=>{
    let active=true;setPreviewLoading(true);setPreviewError(null);
    Promise.resolve().then(()=>realizeProceduralField(stored?.id??previewIdentity,proceduralConfig(draft))).then(items=>{
      validateProceduralAuthoringBounds(items);
      if(active){setNodes(items);setPreviewLoading(false);}
    }).catch(e=>{
      if(active){setNodes([]);setPreviewError(e instanceof Error?e.message:String(e));setPreviewLoading(false);}
    });
    return()=>{active=false;};
  },[draft,stored?.id]);
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
    <section className="production-approval" aria-label="Procedural influence field">
      <h3>Influence field</h3>
      <p className="production-help">Shape a bounded region of the original object centers. Move, resize, orient and tint the affected objects together;
        objects outside the region keep their generated values. Existing individual edits are checked before updating.</p>
      <label className="production-check"><input type="checkbox" aria-label="Enable influence field" checked={draft.influence?.enabled ?? false}
        onChange={event => setDraft(value => ({ ...value, influence: { ...(value.influence ?? rawProceduralInfluence()), enabled: event.target.checked } }))}/>
        Apply an influence field</label>
      {draft.influence && <>
        <div className="production-plan-grid">
          <label className="production-field"><span>Influence shape</span>
            <select aria-label="Influence shape" value={influence.shape} disabled={!influence.enabled}
              onChange={event => patchInfluence({ shape: event.target.value as InfluenceRaw["shape"] })}>
              <option value="radial">Radial region</option><option value="horizontal">Horizontal distance from center</option><option value="vertical">Vertical distance from center</option>
            </select>
          </label>
          <label className="production-field"><span>Influence falloff</span>
            <select aria-label="Influence falloff" value={influence.falloff} disabled={!influence.enabled}
              onChange={event => patchInfluence({ falloff: event.target.value as InfluenceRaw["falloff"] })}>
              <option value="linear">Linear falloff</option><option value="smoothstep">Smooth falloff</option>
            </select>
          </label>
          {influenceDimensions.map(([key, label]) => <label className="production-field" key={key}><span>{label}</span>
            <input aria-label={label} type="text" inputMode="numeric" maxLength={128} disabled={!influence.enabled} value={influence[key]}
              onChange={event => patchInfluence({ [key]: event.target.value })}/>
          </label>)}
          <label className="production-field"><span>Influence center fill</span><input aria-label="Influence center fill" value={influence.fill} maxLength={7}
            disabled={!influence.enabled} onChange={event => patchInfluence({ fill: event.target.value })}/></label>
        </div>
        <p className="production-help">Center values fade toward the unchanged field at the radius. Objects must remain between 4 and 128 pixels and fit the authored stage.
          Each object keeps its orientation throughout its entrance.</p>
        {!influence.enabled && <p className="production-help" role="status">Disabled influence values remain in this local draft. Saving removes the influence from the generator while preserving those fields for further editing.</p>}
      </>}
    </section>
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
      <label className="production-field"><span>Editorial playhead · {renderedTime.toFixed(2)} s</span>
        <input aria-label="Procedural preview playhead" type="range" min={0} max={previewMax}
          step={1/30} value={renderedTime} onChange={e=>setPreviewTime(Number(e.target.value))}/>
      </label>
      {previewLoading?<p role="status">Evaluating bounded procedural preview…</p>
       :previewError?<p role="status">No preview until the field constraints are valid.</p>
       :<CompositionStudy nodes={nodes} width={1920} height={1080} time={renderedTime} background="#0C1723" ink={inheritedTextInk(project.visual_language.palette)} textOverrides={displayProfile?.text_overrides}
          label={`Procedural editorial preview, ${draft.distribution} distribution, ${nodes.length} editable objects`}/>}
      <p className="mono">{draft.count} authored objects · seed {draft.seed} ·
        {stored?` attached field ${stored.id.slice(0,8)}…`:" not yet attached"} · no native render receipt</p>
    </section>
  </section>;
}
