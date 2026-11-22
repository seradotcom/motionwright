import { useEffect, useRef, useState } from "react";
import { ArrowRightLeft, FileCheck2, ShieldAlert } from "lucide-react";
import type { Change, Project } from "./types";
import {
  exportPortableOtioMediaBundle, previewInstalledOtioMediaCut,
  type OtioOriginalMediaExportBinding,
} from "./api";
import {
  inspectOtioCutPreview, readOtioCutProposalFile, otioHasAbsoluteAnchors,
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
  const [fileLoading,setFileLoading]=useState(false);
  const [bridgeInspecting,setBridgeInspecting]=useState(false);
  const [bundlePath,setBundlePath]=useState("");
  const [editedCut,setEditedCut]=useState("edited.otio");
  const [exportDestination,setExportDestination]=useState("");
  const [exportReviewer,setExportReviewer]=useState("");
  const [mediaOwner,setMediaOwner]=useState("");
  const [mediaLicense,setMediaLicense]=useState("");
  const [exportRights,setExportRights]=useState(false);
  const [allowSynthetic,setAllowSynthetic]=useState(false);
  const [exportingBundle,setExportingBundle]=useState(false);
  const [exportAttempted,setExportAttempted]=useState(false);
  const [exportBindings,setExportBindings]=useState<OtioOriginalMediaExportBinding[]>(()=>project.scenes.length
    ?[{scene_id:project.scenes[0].id,asset_id:"",source_path:"",source_start_frame:0}]
    :[]);
  const fileAttempt=useRef(0);
  const [error,setError]=useState<string|null>(null);
  const [status,setStatus]=useState<string|null>(null);
  const absolute=otioHasAbsoluteAnchors(project);
  useEffect(()=>{
    fileAttempt.current++;
    setSelected(null);setLosses(false);setAnchors(false);
    setStatus(null);setError(null);setWorking(false);setFileLoading(false);
    setBridgeInspecting(false);
    setExportingBundle(false);
    setExportAttempted(false);
  },[scope]);
  const videoAssets=project.assets.filter(asset=>
    asset.media_type==="video/mp4"&&/^[0-9a-f]{64}$/.test(asset.content_sha256??""));
  const updateExportBinding=(index:number,change:Partial<OtioOriginalMediaExportBinding>)=>{
    setExportBindings(previous=>previous.map((binding,position)=>
      position===index?{...binding,...change}:binding));
  };
  const exportOriginalMediaBundle=async()=>{
    if(!desktopMode||busy||working||fileLoading||bridgeInspecting||exportingBundle
      ||exportAttempted||!exportDestination.trim()||!exportReviewer.trim()
      ||!mediaOwner.trim()||!mediaLicense.trim()||!exportRights
      ||!exportBindings.length)return;
    const selectedScope=scope;
    const attempt=++fileAttempt.current;
    setExportingBundle(true);setExportAttempted(true);
    setError(null);setStatus(null);setSelected(null);
    setLosses(false);setAnchors(false);
    try{
      const receipt=await exportPortableOtioMediaBundle(project,{
        destination:exportDestination,reviewer_declaration:exportReviewer,
        media_owner:mediaOwner,media_license:mediaLicense,
        authorized:true,redistributable:true,allow_synthetic:allowSynthetic,
        bindings:exportBindings,
      });
      if(latest.current!==selectedScope||fileAttempt.current!==attempt)return;
      setBundlePath(receipt.path);
      setEditedCut("edited.otio");
      setStatus("Original verified OTIO media bundle exported to "+receipt.path+
        ". Open its timeline.otio in your trusted editor, save the edited cut as edited.otio inside that folder, then choose Preview edited OTIO with verified media. MissingReferences remain unbound. No editor was launched or publication approved.");
    }catch(reason){
      if(latest.current===selectedScope&&fileAttempt.current===attempt)
        setError((reason instanceof Error?reason.message:String(reason))+
          " Check whether the destination already exists before choosing any new export. No automatic retry was made.");
    }finally{
      if(latest.current===selectedScope&&fileAttempt.current===attempt)
        setExportingBundle(false);
    }
  };
  const previewOwnerMediaCut=async()=>{
    if(!desktopMode||busy||working||fileLoading||bridgeInspecting||exportingBundle
       ||!bundlePath.trim()||!editedCut.trim())return;
    const selectedScope=scope;
    const attempt=++fileAttempt.current;
    setSelected(null);setRaw("");setLosses(false);setAnchors(false);
    setError(null);setStatus(null);setBridgeInspecting(true);
    try{
      const returned=await previewInstalledOtioMediaCut(project,bundlePath,editedCut);
      if(latest.current!==selectedScope||fileAttempt.current!==attempt)return;
      const preview=inspectOtioCutPreview(project,JSON.stringify(returned.preview));
      setRaw(JSON.stringify(preview));
      setSelected({scope:selectedScope,preview});
      setStatus("The installed bridge verified "+returned.media_refs_verified+
        " original media reference(s) against this saved source. Review the proposed cut and every property loss before your one explicit Save. No media or editor was imported.");
    }catch(reason){
      if(latest.current===selectedScope&&fileAttempt.current===attempt)
        setError(reason instanceof Error?reason.message:String(reason));
    }finally{
      if(latest.current===selectedScope&&fileAttempt.current===attempt)setBridgeInspecting(false);
    }
  };
  const loadProposalFile=async(file:File)=>{
    if(busy||working||fileLoading||bridgeInspecting||exportingBundle)return;
    const selectedScope=scope;
    const attempt=++fileAttempt.current;
    setRaw("");setSelected(null);setLosses(false);setAnchors(false);
    setError(null);setStatus(null);setFileLoading(true);
    try{
      const result=await readOtioCutProposalFile(project,file);
      if(latest.current!==selectedScope||fileAttempt.current!==attempt)return;
      setRaw(result.text);
      setSelected({scope:selectedScope,preview:result.preview});
      setStatus("Loaded "+file.name+" as a bounded review proposal. Confirm losses and the requested cut before committing. No source media, editor or effect was imported.");
    }catch(reason){
      if(latest.current===selectedScope&&fileAttempt.current===attempt)
        setError(reason instanceof Error?reason.message:String(reason));
    }finally{
      if(latest.current===selectedScope&&fileAttempt.current===attempt)setFileLoading(false);
    }
  };
  const inspect=()=>{
    if(bridgeInspecting||exportingBundle)return;
    fileAttempt.current++;
    setError(null);setStatus(null);setSelected(null);
    try {
      const preview=inspectOtioCutPreview(project,raw);
      setSelected({scope,preview});
      setLosses(false);setAnchors(false);
    }catch(reason){setError(reason instanceof Error?reason.message:String(reason));}
  };
  const current=selected?.scope===scope;
  const approved=!!(current&&losses&&(!absolute||anchors)&&
    reviewer.trim()&&rationale.trim()&&desktopMode&&!busy&&!working
    &&!fileLoading&&!bridgeInspecting&&!exportingBundle);
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
    <details className="portable-operation">
      <summary>Create the original media-bound OTIO bundle</summary>
      <p className="portable-runtime-note">
        Start with saved Motionwright MP4 assets and the same original files you own.
        Export creates a new folder containing a source-bound OTIO timeline, original
        media, provenance and property losses. Rights are your declarations, not
        independent authorization; no NLE or publication is launched.
      </p>
      <label>
        <span className="field-label">New OTIO media bundle destination</span>
        <input aria-label="New OTIO media bundle destination" value={exportDestination}
          maxLength={1800} autoComplete="off" spellCheck={false}
          placeholder="Absolute path to a NEW export folder"
          disabled={busy||working||exportingBundle||bridgeInspecting}
          onChange={event=>{
            fileAttempt.current++;setExportDestination(event.target.value);
            setExportAttempted(false);setSelected(null);setStatus(null);setError(null);
          }}/>
      </label>
      <label>
        <span className="field-label">Original source reviewer declaration</span>
        <input aria-label="Original OTIO source reviewer" maxLength={200}
          value={exportReviewer} disabled={busy||exportingBundle||bridgeInspecting}
          onChange={event=>setExportReviewer(event.target.value)}/>
      </label>
      <label>
        <span className="field-label">Original media rights holder</span>
        <input aria-label="Original OTIO media owner" maxLength={250}
          value={mediaOwner} disabled={busy||exportingBundle||bridgeInspecting}
          onChange={event=>setMediaOwner(event.target.value)}/>
      </label>
      <label>
        <span className="field-label">Original media license</span>
        <input aria-label="Original OTIO media license" maxLength={250}
          value={mediaLicense} disabled={busy||exportingBundle||bridgeInspecting}
          onChange={event=>setMediaLicense(event.target.value)}/>
      </label>
      {exportBindings.map((binding,index)=><fieldset key={index} className="portable-operation">
        <legend>Original source media mapping {index+1}</legend>
        <label>
          <span className="field-label">Source scene</span>
          <select aria-label={"Original OTIO source scene "+(index+1)}
            value={binding.scene_id} disabled={busy||exportingBundle||bridgeInspecting}
            onChange={event=>updateExportBinding(index,{scene_id:event.target.value})}>
            <option value="">Choose saved scene</option>
            {project.scenes.map(scene=><option key={scene.id} value={scene.id}>{scene.name}</option>)}
          </select>
        </label>
        <label>
          <span className="field-label">Saved original video asset</span>
          <select aria-label={"Original OTIO video asset "+(index+1)}
            value={binding.asset_id} disabled={busy||exportingBundle||bridgeInspecting}
            onChange={event=>updateExportBinding(index,{asset_id:event.target.value})}>
            <option value="">Choose saved MP4 asset</option>
            {videoAssets.map(asset=><option key={asset.id} value={asset.id}>{asset.name}</option>)}
          </select>
        </label>
        <label>
          <span className="field-label">Original MP4 file matching the saved asset hash</span>
          <input aria-label={"Original OTIO MP4 file path "+(index+1)}
            value={binding.source_path} maxLength={1800} spellCheck={false}
            autoComplete="off" disabled={busy||exportingBundle||bridgeInspecting}
            placeholder="Absolute path to the original MP4 bytes"
            onChange={event=>updateExportBinding(index,{source_path:event.target.value})}/>
        </label>
        <label>
          <span className="field-label">Original source start frame</span>
          <input aria-label={"Original OTIO source start frame "+(index+1)}
            type="number" min={0} max={216000} step={1}
            value={binding.source_start_frame} disabled={busy||exportingBundle||bridgeInspecting}
            onChange={event=>updateExportBinding(index,{
              source_start_frame:Number(event.target.value),
            })}/>
        </label>
        {exportBindings.length>1&&<button type="button" className="button"
          disabled={busy||exportingBundle||bridgeInspecting}
          onClick={()=>setExportBindings(current=>current.filter((_,position)=>position!==index))}>
          Remove media mapping
        </button>}
      </fieldset>)}
      {exportBindings.length<Math.min(32,project.scenes.length)&&<button type="button"
        className="button" disabled={busy||exportingBundle||bridgeInspecting}
        onClick={()=>{
          const next=project.scenes.find(scene=>!exportBindings.some(binding=>binding.scene_id===scene.id));
          if(next)setExportBindings(current=>[...current,{
            scene_id:next.id,asset_id:"",source_path:"",source_start_frame:0,
          }]);
        }}>Bind another original scene</button>}
      <label className="otio-cut-check">
        <input type="checkbox" checked={exportRights}
          disabled={busy||exportingBundle||bridgeInspecting}
          onChange={event=>setExportRights(event.target.checked)}/>
        I confirm I have permission to use, copy and redistribute each selected
        original MP4 in this local portable editorial bundle.
      </label>
      <label className="otio-cut-check">
        <input type="checkbox" checked={allowSynthetic}
          disabled={busy||exportingBundle||bridgeInspecting}
          onChange={event=>setAllowSynthetic(event.target.checked)}/>
        Explicitly permit synthetic or test-origin video inputs.
      </label>
      <button type="button" className="button button-primary"
        disabled={!desktopMode||busy||working||fileLoading||bridgeInspecting
          ||exportingBundle||exportAttempted||!exportRights
          ||!exportDestination.trim()||!exportReviewer.trim()
          ||!mediaOwner.trim()||!mediaLicense.trim()||videoAssets.length===0
          ||exportBindings.length===0||exportBindings.some(binding=>
            !binding.scene_id||!binding.asset_id||!binding.source_path.trim())}
        onClick={()=>void exportOriginalMediaBundle()}>
        {exportingBundle?"Verifying and exporting original media…":"Export original media-bound OTIO bundle"}
      </button>
      {exportAttempted&&!exportingBundle&&<p className="portable-runtime-note">
        This destination has already had one export attempt. Inspect the folder before
        choosing a new destination; this operation is never retried automatically.
      </p>}
    </details>
      <div className="portable-operation-copy">
        <strong>Review an edited OTIO cut against its original media</strong>
        <span>
          Choose the complete original portable media bundle containing its first-party manifest,
          source-project.json and original media, plus the edited .otio file inside that folder.
          Motionwright verifies source identity and media through the embedded bridge; missing
          protected Python or ffprobe runtimes refuse inspection. Nothing is saved by Preview.
        </span>
      </div>
      <label>
        <span className="field-label">Original portable OTIO media bundle folder</span>
        <input type="text" aria-label="Original OTIO media bundle folder"
          value={bundlePath} maxLength={1700} autoComplete="off" spellCheck={false}
          placeholder="Absolute path to the first-party exported media bundle"
          disabled={busy||working||fileLoading||bridgeInspecting||exportingBundle}
          onChange={event=>{
            fileAttempt.current++;setBundlePath(event.target.value);setSelected(null);
            setLosses(false);setAnchors(false);setError(null);setStatus(null);
          }}/>
      </label>
      <label>
        <span className="field-label">Edited OTIO file inside that bundle</span>
        <input type="text" aria-label="Edited OTIO filename in bundle" value={editedCut}
          maxLength={160} autoComplete="off" spellCheck={false}
          placeholder="edited.otio" disabled={busy||working||fileLoading||bridgeInspecting}
          onChange={event=>{
            fileAttempt.current++;setEditedCut(event.target.value);setSelected(null);
            setLosses(false);setAnchors(false);setError(null);setStatus(null);
          }}/>
      </label>
      <button type="button" className="button"
        disabled={!desktopMode||busy||working||fileLoading||bridgeInspecting||exportingBundle
          ||!bundlePath.trim()||!editedCut.trim()}
        onClick={()=>void previewOwnerMediaCut()}>
        <FileCheck2 size={14} aria-hidden="true"/>
        {bridgeInspecting?"Verifying original OTIO media…":"Preview edited OTIO with verified media"}
      </button>
      <div className="portable-operation-copy">
        <strong>Review an existing bridge proposal</strong>
        <span>Select its bounded <code>motionwright.otio-edit-preview/1</code> JSON file
          or paste it below. A pasted declaration cannot authenticate the external media
          independently; both paths still require the owner's explicit review and Save.</span>
      </div>
      <label>
        <span className="field-label">Select the generated OTIO bridge proposal</span>
        <input type="file" accept=".json,application/json"
          aria-label="OTIO bridge proposal JSON file"
          disabled={working||busy||fileLoading||bridgeInspecting||exportingBundle}
          onChange={event=>{
            const file=event.currentTarget.files?.[0];
            event.currentTarget.value="";
            if(file)void loadProposalFile(file);
          }}/>
      </label>
      <label>
        <span className="field-label">Source-bound OTIO preview JSON (manual alternative)</span>
        <textarea aria-label="OTIO imported cut preview JSON" rows={5} spellCheck={false}
          className="mono" value={raw} maxLength={128_000}
          placeholder='{"schema":"motionwright.otio-edit-preview/1", ...}'
          disabled={working||busy||fileLoading||bridgeInspecting} onChange={event=>{
            fileAttempt.current++;
            setRaw(event.target.value);setSelected(null);
            setLosses(false);setAnchors(false);setError(null);setStatus(null);
          }}/>
      </label>
      <button type="button" className="button" disabled={!raw.trim()||busy||working||fileLoading||bridgeInspecting}
        onClick={inspect}><FileCheck2 size={14} aria-hidden="true"/> {fileLoading?"Reading original proposal…":"Review bounded changes"}</button>
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
