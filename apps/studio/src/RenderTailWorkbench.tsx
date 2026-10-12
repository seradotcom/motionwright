import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { Project } from "./types";
import { inspectRenderTailImpact } from "./renderTailImpact";
import type { RenderImpactCause, RenderTailInspectRequest } from "./renderTailImpact";

export default function RenderTailWorkbench({project}:{project:Project}){
  const [cause,setCause]=useState<RenderImpactCause>("scene_visual");
  const [selected,setSelected]=useState<string[]>(()=>project.scenes[0]?[project.scenes[0].id]:[]);
  const [tail,setTail]=useState(30);
  const [asset,setAsset]=useState("");
  const [base,setBase]=useState(project.revision);
  const stale=base!==project.revision;
  useEffect(()=>{
    setBase(project.revision);
    setSelected(project.scenes[0]?[project.scenes[0].id]:[]);
    setCause("scene_visual");setAsset("");
    // Same-generation revisions deliberately preserve the editor input.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation,project.id]);
  const audio=project.assets.filter(item=>item.media_type.startsWith("audio/") &&
    /^[0-9a-f]{64}$/.test(item.content_sha256??""));
  const request:RenderTailInspectRequest={
    expected_generation:project.generation,expected_revision:base,
    changed_scene_ids:cause==="global_mix"?[]:selected,
    cause,tail_frames:cause==="scene_audio_tail"?tail:0,
    tail_source_asset_id:cause==="scene_audio_tail"?(asset||null):null,
  };
  const [report,error]=useMemo(()=>{
    try{return [inspectRenderTailImpact(project,request),null] as const;}
    catch(e){return [null,e instanceof Error?e.message:String(e)] as const;}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project,base,cause,selected,tail,asset]);
  function toggle(id:string,checked:boolean){
    setSelected(before=>checked?[...before,id]:before.filter(value=>value!==id));
  }
  return <div className="production-split">
    <section className="production-inspector" aria-label="Render dependency source controls">
      <div className="production-section-title"><div>
        <h2>Render dependency impact</h2><p>Source-timeline frame windows · preflight only</p>
      </div></div>
      <p className="production-help">
        Inspect which original scene intervals require re-review after an edit.
        These are source-timeline frames, not confirmed output-cut coordinates.
        No native render cache is invalidated or reused by this inspector.
      </p>
      {stale&&<div className="production-notice" role="status">
        <strong>Source revision changed from {base} to {project.revision}.</strong>
        <span>The earlier dependency estimate is stale.</span>
        <button className="secondary-button" onClick={()=>setBase(project.revision)}>
          <RefreshCw size={14}/> Inspect current revision</button>
      </div>}
      <label className="production-field"><span>Source change category</span>
        <select aria-label="Render impact cause" value={cause}
          onChange={event=>setCause(event.target.value as RenderImpactCause)}>
          <option value="scene_visual">Edited visual source in selected scene</option>
          <option value="scene_audio_tail">Declared scene audio tail / bounded</option>
          <option value="time_ripple">Scene duration / time ripple</option>
          <option value="global_mix">Global audio mix / entire source</option>
        </select>
      </label>
      {cause!=="global_mix"&&<fieldset className="production-field">
        <legend>Changed source scenes</legend>
        <div className="production-capsule-list">
          {project.scenes.map(scene=><label key={scene.id} className="production-check">
            <input type="checkbox" aria-label={"Changed source scene "+scene.name}
              checked={selected.includes(scene.id)}
              onChange={event=>toggle(scene.id,event.target.checked)}/>
            {scene.name}
          </label>)}
        </div>
      </fieldset>}
      {cause==="scene_audio_tail"&&<>
        <label className="production-field"><span>Owner-declared trailing frames (1–300)</span>
          <input type="number" aria-label="Declared audio tail frames" min={1} max={300}
            step={1} value={tail} onChange={event=>setTail(Number(event.target.value))}/>
        </label>
        <label className="production-field"><span>Content-addressed audio source asset</span>
          <select aria-label="Audio tail source asset" value={asset}
            onChange={event=>setAsset(event.target.value)}>
            <option value="">Choose an existing source asset</option>
            {audio.map(item=><option key={item.id} value={item.id}>
              {item.name} · {item.content_sha256?.slice(0,12)}…</option>)}
          </select>
        </label>
        <p className="production-help">
          Tail length is not independently measured from its waveform.
          Audio source identity is required; absent audio input is rejected.
        </p>
      </>}
      <p className="production-help">
        Project generation {project.generation.slice(0,12)}… · inspected revision {base}.
        Global mixing and timing ripple conservatively mark broad dependent windows.
      </p>
      {error&&<p role="alert" className="production-error">{error}</p>}
    </section>
    <section className="production-viewer" aria-label="Render dependency impact report">
      <div className="production-viewer-toolbar"><div>
        <strong>Source-frame impact windows</strong>
        <span>{report?report.profiles.length+" deliverable profiles":"Awaiting valid source"}</span>
      </div></div>
      {report&&<div className="production-evidence-note">
        <p><strong>Dependency preflight / technical comparison not run.</strong>
          Frame intervals are half-open at exact rational FPS and refer to the
          original project timeline.</p>
        <p>No native frames regenerated or avoided. No saved renderer wallclock measured.
          No creative approval inferred.</p>
        <div className="production-capsule-list">
          {report.profiles.map(profile=>{
            const delivery=project.deliverables.find(p=>p.id===profile.deliverable_id);
            return <article key={profile.deliverable_id}>
              <header><strong>{delivery?.name??profile.deliverable_id}</strong>
                <span>{profile.frame_rate.num}/{profile.frame_rate.den} FPS</span></header>
              <p>Source scenes: {profile.affected_scene_ids.length?
                profile.affected_scene_ids.map(id=>project.scenes.find(scene=>scene.id===id)?.name??id).join(", "):
                "none in this delivery"}</p>
              <p className="mono" aria-label={"Dirty source frames "+(delivery?.name??profile.deliverable_id)}>
                {profile.source_timeline_windows.length?
                  profile.source_timeline_windows.map(range=>"["+range.start+", "+range.end+")").join(" · "):
                  "No source windows selected"}
              </p>
              {profile.is_scoped_selection&&<p>Scoped selection: output-cut frame mapping not verified.</p>}
              {profile.exceeds_source_duration&&<p className="production-error">
                Declared audio tail extends beyond film duration; output duration review required.</p>}
            </article>;
          })}
        </div>
      </div>}
    </section>
  </div>;
}
