import { useEffect, useRef, useState } from "react";
import { ArrowRightLeft, FileCheck2, ShieldAlert } from "lucide-react";
import type { Change, Project } from "./types";
import {
  inspectOtioCutPreview, otioHasAbsoluteAnchors,
  type OtioEditPreview, type OtioOwnerCutReview,
} from "./otioCutReview";

/** A review station inside the existing Deliver work surface, not an NLE or
 * an external-file executor. A source-verified Python bridge precedes it. */
export default function OtioCutReviewWorkbench({
  project, commit, busy, desktopMode,
}:{
  project:Project;commit:(change:Change)=>Promise<void>;
  busy:boolean;desktopMode:boolean;
}){
  const scope=[project.id,project.generation,project.revision].join(":");
  const latest=useRef(scope);latest.current=scope;
  const [raw,setRaw]=useState("");
  const [selected,setSelected]=useState<{scope:string;preview:OtioEditPreview}|null>(null);
  const [reviewer,setReviewer]=useState("");
  const [rationale,setRationale]=useState("");
  const [losses,setLosses]=useState(false);
  const [anchors,setAnchors]=useState(false);
  const [working,setWorking]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [status,setStatus]=useState<string|null>(null);
  const absolute=otioHasAbsoluteAnchors(project);
  useEffect(()=>{
    setSelected(null);setLosses(false);setAnchors(false);
    setStatus(null);setError(null);setWorking(false);
  },[scope]);
  const inspect=()=>{
    setError(null);setStatus(null);setSelected(null);
    try {
      const preview=inspectOtioCutPreview(project,raw);
      setSelected({scope,preview});
      setLosses(false);setAnchors(false);
    }catch(reason){setError(reason instanceof Error?reason.message:String(reason));}
  };
  const current=selected?.scope===scope;
  const approved=!!(current&&losses&&(!absolute||anchors)&&
    reviewer.trim()&&rationale.trim()&&desktopMode&&!busy&&!working);
  const apply=async()=>{
    if(!approved || !selected || latest.current!==scope)return;
    if(!desktopMode){setError("The browser demo cannot commit external cut proposals.");return;}
    setWorking(true);setError(null);setStatus(null);
    const review:OtioOwnerCutReview={
      preview:selected.preview,reviewer_declaration:reviewer.trim(),rationale:rationale.trim(),
      acknowledges_property_losses:true,
      acknowledges_absolute_audio_marker_positions:anchors,
      confirms_single_revision_change:true,
    };
    try{
      await commit({type:"apply_otio_owner_cut_review",review});
      if(latest.current===scope){
        setSelected(null);
        setStatus("Reviewed cut committed in one canonical project revision. No source media, renderer or NLE effect imported.");
      }
    }catch(reason){
      if(latest.current===scope)setError(reason instanceof Error?reason.message:String(reason));
    }finally{
      if(latest.current===scope)setWorking(false);
    }
  };
  const names=new Map(project.scenes.map(scene=>[scene.id,scene.name]));
  return <section className="portable-project otio-cut-review" aria-label="OTIO owner cut review">
    <header className="portable-heading">
      <div>
        <strong><ArrowRightLeft size={16} aria-hidden="true"/> OTIO cut review</strong>
        <span>Accept an exact cut proposal without losing native objects or media provenance.</span>
      </div>
      <span className="status-pill">OWNER REVIEW REQUIRED</span>
    </header>
    <div className="portable-operation">
      <div className="portable-operation-copy">
        <strong>Inspect a bounded external cut</strong>
        <span>
          Run <code>tooling/otio-media/bridge.py preview-import</code> against the current
          source snapshot, then paste its <code>motionwright.otio-edit-preview/1</code> JSON.
          This desk checks scene identity and the native edit vocabulary; it cannot
          authenticate third-party files or independently confirm the bridge's media checks.
        </span>
      </div>
      <label>
        <span className="field-label">Source-bound OTIO preview JSON</span>
        <textarea aria-label="OTIO imported cut preview JSON" rows={5} spellCheck={false}
          className="mono" value={raw} maxLength={128_000}
          placeholder='{"schema":"motionwright.otio-edit-preview/1", ...}'
          disabled={working||busy} onChange={event=>{
            setRaw(event.target.value);setSelected(null);
            setLosses(false);setAnchors(false);setError(null);setStatus(null);
          }}/>
      </label>
      <button type="button" className="button" disabled={!raw.trim()||busy||working}
        onClick={inspect}><FileCheck2 size={14} aria-hidden="true"/> Review bounded changes</button>
    </div>
    {!desktopMode&&<p className="portable-runtime-note" role="status">
      Browser demo: proposals can be inspected, never committed.
    </p>}
    {current&&selected&&<section aria-label="OTIO proposed revision" className="portable-plan">
      <div className="portable-operation-copy">
        <strong>Proposed single revision · {selected.preview.native_change_proposals.length} edit(s)</strong>
        <span>Source r{selected.preview.expected_project_revision} · {selected.preview.source_project_sha256.slice(0,14)}… ·
          {selected.preview.media_refs_verified} bridge-declared verified source(s) and
          {selected.preview.missing_references_retained} missing reference(s). Media bytes are not imported here.
        </span>
      </div>
      <ol className="otio-cut-order" aria-label="Proposed final scene order">
        {selected.preview.proposed_scene_order.map((id,index)=><li key={id}>
          <span className="mono">{String(index+1).padStart(2,"0")}</span>
          <span>{names.get(id)??"Unknown original scene"}</span>
          <span className="field-label">was {project.scenes.findIndex(scene=>scene.id===id)+1}</span>
        </li>)}
      </ol>
      <ul aria-label="Exact supported OTIO edits" className="otio-cut-edits">
        {selected.preview.native_change_proposals.map((operation,index)=><li key={index}>
          {operation.type==="move_scene"
            ? `Move ${names.get(operation.scene_id)} to position ${operation.to_index+1}`
            : `End-trim ${names.get(operation.scene_id)} to ${operation.duration.num}/${operation.duration.den} seconds`}
        </li>)}
      </ul>
      <details open>
        <summary>Property losses and unsupported NLE features · {selected.preview.property_fidelity.length} classifications</summary>
        <ul className="otio-cut-losses">{selected.preview.loss_report.map((description,index)=><li key={index}>{description}</li>)}</ul>
        <p className="portable-runtime-note"><ShieldAlert size={14} aria-hidden="true"/>
          Native camera, typography, audio, captions, locks, source in-points, renderer effects,
          external identity and publishing rights are not copied from OTIO.</p>
      </details>
      <div className="otio-cut-approval" aria-label="Explicit owner cut approval">
        <label><span className="field-label">Reviewer's declared identity (not independently authenticated)</span>
          <input aria-label="OTIO reviewer declaration" value={reviewer} maxLength={160}
            disabled={busy||working} onChange={event=>setReviewer(event.target.value)}/></label>
        <label><span className="field-label">Reason for accepting exactly these changes</span>
          <textarea aria-label="OTIO review rationale" rows={2} value={rationale} maxLength={1600}
            disabled={busy||working} onChange={event=>setRationale(event.target.value)}/></label>
        <label className="otio-cut-check">
          <input type="checkbox" checked={losses} disabled={busy||working}
            onChange={event=>setLosses(event.target.checked)}/>
          I reviewed and accept the listed property losses. No external media, effects or permissions are imported.
        </label>
        {absolute&&<label className="otio-cut-check">
          <input type="checkbox" checked={anchors} disabled={busy||working}
            onChange={event=>setAnchors(event.target.checked)}/>
          I acknowledge existing audio, transcript or marker timestamps remain absolute and may need manual reconforming.
        </label>}
        <div className="portable-actions">
          <button className="button button-primary" type="button" disabled={!approved}
            onClick={()=>void apply()}>{working?"Committing…":"Commit reviewed OTIO cut · one revision"}</button>
          <button className="button" type="button" disabled={busy||working}
            onClick={()=>{setSelected(null);setLosses(false);setAnchors(false);}}>Discard proposal</button>
        </div>
      </div>
    </section>}
    {error&&<p className="portable-error" role="alert">{error}</p>}
    {status&&<p role="status">{status}</p>}
  </section>;
}
