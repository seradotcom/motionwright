import NativeInspectionWorkbench from "./NativeInspectionWorkbench";
import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Check, FileBox, Layers, Plus, RefreshCw, Save } from "lucide-react";
import type { CanvasNode, Change, Project, Scene, DeliverableProfile, MotionCanvasRenderEvidence } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { defaultHeroConfig, emptyProductionDesign, realizeProductHero, sameValue, planContentDigest } from "./creativeProduction";
import type { HeroConfig, ProductionPlan, NarrativeEvidenceKind, NativeCapsule } from "./creativeProduction";
import CreativePatchWorkbench from "./CreativePatchWorkbench";
import "./production-design.css";

type Commit = (change: Change) => Promise<void>;
const previewIdentity = "00000000-0000-4000-8000-000000000005";
const aspects = [{ label:"16:9", width:1920,height:1080 },{ label:"9:16",width:1080,height:1920 },{label:"1:1",width:1080,height:1080}];

function RevisionNotice({ base, current, onReload }: { base:number; current:number; onReload:()=>void }) {
  return base !== current ? <div className="production-notice" role="status">
    <strong>This draft is based on revision {base}; the project is now {current}.</strong>
    <span>Your input has not been overwritten. Reload the current revision before applying another change.</span>
    <button className="secondary-button" onClick={onReload}><RefreshCw size={14}/> Reload current values</button>
  </div> : null;
}
function HeroWorkbench({ project, scene, commit, busy, playhead, onSeek, onOpenCanvas }: {
  project:Project; scene:Scene|null; commit:Commit; busy:boolean; playhead:number; onSeek:(time:number)=>void; onOpenCanvas:()=>void;
}) {
  const instance = project.production_design?.heroes.find(hero => hero.scene_id === scene?.id);
  const [draft,setDraft] = useState<HeroConfig>(() => structuredClone(instance?.config ?? defaultHeroConfig()));
  const [base,setBase] = useState(project.revision);
  const [aspect,setAspect] = useState(aspects[0]);
  const [nodes,setNodes] = useState<CanvasNode[]>([]);
  const [error,setError] = useState<string|null>(null);
  const [previewLoading,setPreviewLoading] = useState(true);
  const reload = () => { setDraft(structuredClone(instance?.config ?? defaultHeroConfig())); setBase(project.revision); setError(null); };
  useEffect(() => {
    setDraft(structuredClone(instance?.config ?? defaultHeroConfig())); setBase(project.revision);
    if (scene) onSeek(seconds(scene.start)+Math.min(2,Math.max(0,seconds(scene.duration)-1/30)));
    // Scene changes explicitly reset the form. Revision changes do not discard unsaved input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene?.id,project.generation]);
  useEffect(() => {
    if (instance && sameValue(instance.config,draft)) setBase(project.revision);
  }, [instance,project.revision,draft]);
  useEffect(() => {
    let current = true; setPreviewLoading(true); setError(null);
    realizeProductHero(instance?.id ?? previewIdentity,draft,aspect.width,aspect.height).then(value => {
      if (current) { setNodes(value); setPreviewLoading(false); }
    }, reason => { if (current) { setNodes([]); setError(String(reason instanceof Error ? reason.message : reason)); setPreviewLoading(false); } });
    return () => { current=false; };
  }, [draft,aspect,instance?.id]);
  const localTime = scene ? Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))) : 2;
  const background = project.visual_language.palette.find(t => ["surface","background"].includes(t.name.toLowerCase()))?.value ?? "#0F1216";
  const locked = project.locks.some(lock => ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource) && ["content","position","style","timing","renderer"].includes(lock.kind));
  const canApply = !!scene && scene.renderer === "motion-canvas" && seconds(scene.duration)>=2 && !busy && !error && !previewLoading && base===project.revision && !locked;
  return <div className="production-split">
    <section className="production-inspector" aria-label="ProductHeroReveal controls">
      <div className="production-section-title"><Layers size={16}/><div><h2>ProductHeroReveal</h2><p>Editable first-party study · v1</p></div></div>
      <span className="production-status">CREATIVE REVIEW REQUIRED</span>
      <p className="production-help">Six persistent objects, synchronized cubic entrances and a responsive composition. This is a graphic study, not a fabricated product screenshot.</p>
      <RevisionNotice base={base} current={project.revision} onReload={reload}/>
      {([ ["eyebrow","Eyebrow",48], ["headline","Headline",64], ["body","Supporting copy",150], ["wordmark","Wordmark",8] ] as const).map(([field,label,max]) =>
        <label className="production-field" key={field}><span>{label}<small>{Array.from(draft[field]).length}/{max}</small></span>
          {field === "headline" || field === "body" ? <textarea aria-label={label} rows={field==="headline"?3:4} value={draft[field]} onChange={e => setDraft({...draft,[field]:e.target.value})}/>
            : <input aria-label={label} value={draft[field]} onChange={e => setDraft({...draft,[field]:e.target.value})}/>} </label>)}
      <div className="production-color-fields">
        <label className="production-field"><span>Foreground</span><input aria-label="Hero foreground" type="color" value={draft.foreground} onChange={e => setDraft({...draft,foreground:e.target.value})}/></label>
        <label className="production-field"><span>Accent</span><input aria-label="Hero accent" type="color" value={draft.accent} onChange={e => setDraft({...draft,accent:e.target.value})}/></label>
      </div>
      <label className="production-check"><input type="checkbox" checked={draft.motion} onChange={e => setDraft({...draft,motion:e.target.checked})}/> Animate entrance</label>
      <p className="production-help">Turning motion off creates a static equivalent. Updating the component preserves compatible human overrides and rejects conflicts.</p>
      {error && <p className="production-error" role="alert">{error}</p>}
      {!scene && <button className="primary-button" disabled={busy} onClick={() => commit({type:"add_scene",name:"Product hero",objective:"An original, editable product reveal",duration_seconds:6})}><Plus size={14}/> Create a six-second scene</button>}
      {scene && (scene.renderer!=="motion-canvas" || seconds(scene.duration)<2) && <p className="production-error">Select a Motion Canvas scene at least two seconds long. Existing scene settings will not be replaced automatically.</p>}
      <button className="primary-button" disabled={!canApply} onClick={() => commit({ type:"upsert_product_hero",instance_id:instance?.id ?? crypto.randomUUID(),scene_id:scene!.id,config:draft })}>
        <Save size={14}/>{instance?"Update component":"Add to scene"}</button>
      {instance && <div className="production-button-row"><button className="secondary-button" onClick={onOpenCanvas}><ArrowUpRight size={14}/> Edit objects</button>
        <button className="text-button" disabled={busy || locked} onClick={() => commit({type:"detach_product_hero",instance_id:instance.id})}>Detach, keep objects</button></div>}
    </section>
    <section className="production-viewer" aria-label="Component study viewer">
      <div className="production-viewer-toolbar"><div><strong>Composition study</strong><span>{sameValue(draft,instance?.config)?"Stored parameters":"Uncommitted parameters"} · editorial preview</span></div>
        <div className="production-segmented" aria-label="Preview aspect">{aspects.map(value => <button key={value.label} aria-pressed={value.label===aspect.label} onClick={() => setAspect(value)}>{value.label}</button>)}</div></div>
      <div className={"production-preview-mat " + (aspect.height>aspect.width?"portrait":"")}>
        {previewLoading ? <p role="status">Preparing the editable study…</p> : <CompositionStudy nodes={nodes} width={aspect.width} height={aspect.height} time={localTime} background={background} label={`ProductHeroReveal ${aspect.label}, editorial frame at ${localTime.toFixed(3)} seconds`}/>}
      </div>
      <div className="production-transport"><span className="mono">{localTime.toFixed(3)} s</span>
        <input aria-label="Component playhead" type="range" min={0} max={scene?seconds(scene.duration):6} step={1/30} value={localTime} disabled={!scene} onChange={e => onSeek(seconds(scene!.start)+Number(e.target.value))}/>
        <button className="secondary-button" disabled={!scene} onClick={() => onSeek(seconds(scene!.start)+.65)}>Entrance</button>
        <button className="secondary-button" disabled={!scene} onClick={() => onSeek(seconds(scene!.start)+Math.min(2,seconds(scene!.duration)-1/30))}>Settled</button></div>
      <div className="production-evidence-note"><strong>{aspect.width} × {aspect.height} · project revision {project.revision}</strong>
        <p>This parameter study does not replace custom edits on scene objects. Use Canvas to inspect those overrides. This preview audits composition and timing intent. Use the native frame stage and Deliver for authoritative renderer frames and export verification. Glyph metrics, clipping and creative approval are separate checks.</p></div>
    </section>
  </div>;
}

function defaultPlan(project:Project):ProductionPlan {
  return {objective:project.brief.objective || "",audience:project.brief.audience || "",concept:project.narrative.premise || "",reference_constraints:[],exclusions:["Do not present a graphic study as observed product behavior."],
    shots:project.scenes.map(scene => ({scene_id:scene.id,purpose:scene.objective || scene.name,claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"})),clock:{kind:"timeline"},approval:null};
}
function PlanWorkbench({project,commit,busy}:{project:Project;commit:Commit;busy:boolean}) {
  const stored = project.production_design?.plan;
  const [draft,setDraft] = useState<ProductionPlan>(() => structuredClone(stored ?? defaultPlan(project)));
  const [base,setBase] = useState(project.revision);
  const [reviewer,setReviewer] = useState(""); const [note,setNote] = useState(""); const [error,setError] = useState<string|null>(null);
  const reload = () => {setDraft(structuredClone(stored ?? defaultPlan(project)));setBase(project.revision);setError(null);};
  useEffect(() => { if (stored && sameValue(stored,draft)) setBase(project.revision); },[stored,draft,project.revision]);
  const patch = (value:Partial<ProductionPlan>) => setDraft({...draft,...value,approval:null});
  const cleanDraft = ():ProductionPlan => ({...draft,reference_constraints:draft.reference_constraints.map(v=>v.trim()).filter(Boolean),exclusions:draft.exclusions.map(v=>v.trim()).filter(Boolean),approval:null});
  const approve = async () => {
    try { const cleaned=cleanDraft(); const approval={reviewer,note,content_sha256:await planContentDigest(cleaned)}; setDraft({...cleaned,approval}); await commit({type:"set_production_plan",plan:{...cleaned,approval}}); }
    catch (reason) {setError(reason instanceof Error?reason.message:String(reason));}
  };
  return <div className="production-plan">
    <div className="production-section-title"><Check size={18}/><div><h2>Production plan</h2><p>Narrative, evidence, reference constraints and the governing clock.</p></div></div>
    <RevisionNotice base={base} current={project.revision} onReload={reload}/>
    <div className="production-plan-grid"><label className="production-field"><span>Objective</span><textarea rows={3} value={draft.objective} onChange={e => patch({objective:e.target.value})}/></label>
      <label className="production-field"><span>Audience</span><textarea rows={3} value={draft.audience} onChange={e => patch({audience:e.target.value})}/></label></div>
    <label className="production-field"><span>Creative concept</span><textarea rows={4} value={draft.concept} onChange={e => patch({concept:e.target.value})}/></label>
    <div className="production-plan-grid"><label className="production-field"><span>Reference-derived constraints · one per line</span><textarea rows={4} value={draft.reference_constraints.join("\n")} onChange={e => patch({reference_constraints:e.target.value.split("\n")})}/></label>
      <label className="production-field"><span>Exclusions · one per line</span><textarea rows={4} value={draft.exclusions.join("\n")} onChange={e => patch({exclusions:e.target.value.split("\n")})}/></label></div>
    <label className="production-field"><span>Governing clock</span><select value={draft.clock.kind === "voice" ? draft.clock.voice_track_id : draft.clock.kind} onChange={e => patch({clock:e.target.value === "timeline" ? {kind:"timeline"} : {kind:"voice",voice_track_id:e.target.value}})}>
      <option value="timeline">Timeline</option>{project.audio.voice_tracks.map(track => <option key={track.id} value={track.id}>Measured voice · {track.id.slice(0,8)}</option>)}
      {draft.clock.kind === "music" && <option value="music" disabled>Measured music clock · retained</option>}</select></label>
    <div className="production-shot-list"><h3>Shot and evidence plan</h3>{draft.shots.map((shot,i) => <div className="production-shot" key={shot.scene_id}>
      <strong>{String(i+1).padStart(2,"0")} / {project.scenes.find(s => s.id===shot.scene_id)?.name ?? "Missing scene"}</strong>
      <label className="production-field"><span>Purpose</span><textarea rows={2} value={shot.purpose} onChange={e => patch({shots:draft.shots.map((s,j)=>j===i?{...s,purpose:e.target.value}:s)})}/></label>
      <label className="production-field"><span>Evidence classification</span><select value={shot.evidence_kind} onChange={e => patch({shots:draft.shots.map((s,j)=>j===i?{...s,evidence_kind:e.target.value as NarrativeEvidenceKind}:s)})}>
        <option value="graphic_study">Graphic study · not proof of behavior</option><option value="real_capture">Real capture · source required</option><option value="licensed_footage">Licensed footage · source required</option><option value="synthetic_labeled">Synthetic · labeled</option></select></label>
      <label className="production-field"><span>Source asset</span><select value={shot.asset_ids[0] ?? ""} onChange={e => patch({shots:draft.shots.map((s,j)=>j===i?{...s,asset_ids:e.target.value?[e.target.value]:[]}:s)})}><option value="">No source attached</option>{project.assets.filter(a=>a.content_sha256).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      {!!project.brief.claims.length && <fieldset className="production-claims"><legend>Claims this shot supports</legend>{project.brief.claims.map(claim=><label key={claim.id}><input type="checkbox" checked={shot.claim_ids.includes(claim.id)} onChange={e=>patch({shots:draft.shots.map((s,j)=>j===i?{...s,claim_ids:e.target.checked?[...s.claim_ids,claim.id]:s.claim_ids.filter(id=>id!==claim.id)}:s)})}/>{claim.text}</label>)}</fieldset>}
    </div>)}</div>
    <div className="production-button-row"><button className="primary-button" disabled={busy || base!==project.revision} onClick={() => {const cleaned=cleanDraft();setDraft(cleaned);return commit({type:"set_production_plan",plan:cleaned});}}><Save size={14}/> Save production plan</button>
      <button className="secondary-button" disabled={busy} onClick={() => patch({shots:project.scenes.map(s => draft.shots.find(shot=>shot.scene_id===s.id) ?? {scene_id:s.id,purpose:s.objective || s.name,claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"})})}>Refresh shot list</button></div>
    <section className="production-approval"><h3>Content-bound concept approval</h3><p>A creative decision only. It does not approve renderer fidelity, capture provenance, rights or technical delivery.</p>
      <div className="production-plan-grid"><label className="production-field"><span>Reviewer</span><input value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label><label className="production-field"><span>Approval note</span><input value={note} onChange={e=>setNote(e.target.value)}/></label></div>
      <button className="secondary-button" disabled={busy || !reviewer.trim() || !note.trim() || base!==project.revision} onClick={approve}>Approve this concept digest</button>
      {stored?.approval && <p className="mono">Recorded for {stored.approval.content_sha256.slice(0,16)}… by {stored.approval.reviewer}</p>}{error && <p className="production-error" role="alert">{error}</p>}
    </section>
  </div>;
}

function SourcesWorkbench({project,scene,commit,busy}:{project:Project;scene:Scene|null;commit:Commit;busy:boolean}) {
  const [assetId,setAssetId]=useState(""); const [renderer,setRenderer]=useState("hyperframes"); const [version,setVersion]=useState("unverified");
  const asset = project.assets.find(a=>a.id===assetId);
  const capsules = project.production_design?.capsules ?? [];
  const attach = () => {
    if (!scene || !asset?.content_sha256) return;
    const capsule:NativeCapsule={id:crypto.randomUUID(),scene_id:scene.id,source_asset_id:asset.id,source_sha256:asset.content_sha256,label:asset.name,
      fidelity:{renderer,renderer_version:version,visual:"unavailable",temporal:"unavailable",structural:"native",editable:"unavailable",losses:["Source preserved as an opaque attachment. Parameter editing and renderer execution are not admitted."],evidence_sha256:null},editable_parameters:[],native_editor_hint:"Open with the original application's trusted environment"};
    return commit({type:"upsert_native_capsule",capsule});
  };
  return <div className="production-sources"><div className="production-section-title"><FileBox size={18}/><div><h2>Native source capsules</h2><p>Attach first. Preserve source bytes before considering translation or baking.</p></div></div>
    <p className="production-help">Select an already imported source asset. This operation preserves its digest and attachment identity; it does not install a runtime, execute source code or claim editable interoperability.</p>
    <div className="production-plan-grid"><label className="production-field"><span>Imported source asset</span><select value={assetId} onChange={e=>setAssetId(e.target.value)}><option value="">Choose a digest-bound asset</option>{project.assets.filter(a=>a.content_sha256).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="production-field"><span>Native renderer</span><input value={renderer} onChange={e=>setRenderer(e.target.value)}/></label><label className="production-field"><span>Source renderer version</span><input value={version} onChange={e=>setVersion(e.target.value)}/></label></div>
    <button className="primary-button" disabled={busy || !scene || !asset?.content_sha256 || !renderer.trim() || !version.trim()} onClick={attach}><Plus size={14}/> Attach source to scene</button>
    <div className="production-capsule-list">{capsules.map(c=><article key={c.id}><header><strong>{c.label}</strong><span>{c.fidelity.renderer} / {c.fidelity.renderer_version}</span></header>
      <p className="mono">sha256:{c.source_sha256}</p><dl>{(["visual","temporal","structural","editable"] as const).map(d=><div key={d}><dt>{d}</dt><dd>{c.fidelity[d]}</dd></div>)}</dl>
      {c.fidelity.losses.map(loss=><p key={loss}>{loss}</p>)}<small>Declared support metadata, not an independently verified renderer receipt.</small></article>)}</div>
  </div>;
}

export default function ProductionDesignWorkspace(props:{project:Project;scene:Scene|null;commit:Commit;busy:boolean;playhead:number;onSeek:(time:number)=>void;onSelectScene:(id:string)=>void;onOpenCanvas:()=>void;displayProfile:DeliverableProfile|null;evidence:MotionCanvasRenderEvidence|null}) {
  const [tab,setTab]=useState<"component"|"plan"|"sources"|"patch"|"inspection">("component");
  const design=props.project.production_design ?? emptyProductionDesign();
  const sceneOptions=useMemo(()=>props.project.scenes.map(scene=><option key={scene.id} value={scene.id}>{scene.name}</option>),[props.project.scenes]);
  return <section className="production-workspace" aria-label="Creative production workstation">
    <header className="production-workspace-header"><div><span className="eyebrow">CREATIVE PRODUCTION</span><h1>Direct the work. Preserve the decisions.</h1><p>{design.heroes.length} component{design.heroes.length===1?"":"s"} · {design.capsules.length} native source{design.capsules.length===1?"":"s"} · revision {props.project.revision}</p></div>
      <label className="production-scene-select"><span>Working scene</span><select aria-label="Production working scene" value={props.scene?.id ?? ""} onChange={e=>props.onSelectScene(e.target.value)}><option value="" disabled>Select a scene</option>{sceneOptions}</select></label></header>
    <nav className="production-tabs" aria-label="Production tools">{([ ["component","Components"],["plan","Production plan"],["sources","Native sources"],["patch","Scoped changes"],["inspection","Native inspection"] ] as const).map(([key,label])=><button key={key} aria-current={tab===key?"page":undefined} onClick={()=>setTab(key)}>{label}</button>)}</nav>
    {tab==="component"?<HeroWorkbench {...props}/>:tab==="plan"?<PlanWorkbench key={props.project.generation} {...props}/>:tab==="sources"?<SourcesWorkbench {...props}/>:tab==="inspection"?<NativeInspectionWorkbench {...props}/>:<CreativePatchWorkbench {...props}/>}
  </section>;
}
