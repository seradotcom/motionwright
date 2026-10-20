import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, RefreshCw, Save } from "lucide-react";
import type { Change, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { bootstrap } from "./api";
import { inheritedTextInk } from "./canvasInheritedInk";
import { CAMERA_REFRAME_VERSION, cameraPreviewOffset, validateCameraReframe } from "./cameraReframe";
import type { CameraReframe } from "./cameraReframe";
import {
  armCameraDraft, cameraDraftChanged, cameraDraftValue,
  cameraFields, cameraRecoveryKey, cameraScope, cameraSessionIssue,
  confirmCameraOutcomeAbsent, createCameraDraft, decodeCameraCopy,
  editCameraDraft, listCameraCopies, loadCameraCopy,
  rebaseCameraDraft, reconcileCameraDraft, writeCameraCopy,
} from "./cameraReframeDraft";
import type { CameraDraft, CameraField } from "./cameraReframeDraft";
type Props={project:Project;scene:Scene|null;busy:boolean;displayProfile?:Project["deliverables"][number]|null;
  commit:(change:Change)=>Promise<void>;playhead:number;
  onSeek:(time:number)=>void;onOpenCanvas:()=>void;};
function newDraft(scene:Scene|null):CameraReframe{
  const center=scene?.camera??{center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  return {id:crypto.randomUUID(),scene_id:scene?.id??crypto.randomUUID(),
    component_version:CAMERA_REFRAME_VERSION,start_center_x:center.center_x,
    start_center_y:center.center_y,end_center_x:center.center_x+12,
    end_center_y:center.center_y+4,duration_frames:90};
}
function message(error:unknown):string{return error instanceof Error?error.message:String(error);}
export default function CameraReframeWorkbench({project,scene,busy,commit,playhead,onSeek,onOpenCanvas,displayProfile}:Props){
  const stored=project.production_design?.camera_reframes?.find(reframe=>reframe.scene_id===scene?.id);
  const scope=scene?cameraScope(project,scene):null;
  const writer=useRef<string>(crypto.randomUUID());
  const lastVerifiedRaw=useRef<string|null>(null);
  const [author,setAuthor]=useState<CameraDraft|null>(()=>{
    if(!scene)return null;
    return createCameraDraft(project,scene,stored??newDraft(scene));
  });
  const [error,setError]=useState<string|null>(null);
  const [backupWarning,setBackupWarning]=useState<string|null>(null);
  const [copies,setCopies]=useState<string[]>([]);
  const [pending,setPending]=useState(false);
  const inFlight=useRef(false);
  const active=scene&&author?.scope===scope?author:null;
  const fields=active?.fields??cameraFields(stored??newDraft(scene));
  let parsed:CameraReframe|null=null,parseIssue:string|null=null;
  try {if(active)parsed=cameraDraftValue(active);}
  catch(reason){parseIssue=message(reason);}
  const draft=parsed??stored??newDraft(scene);
  const stale=!!active&&active.baseRevision!==project.revision;
  const locked=project.locks.some(lock=>
    ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource) &&
    ["content","position","timing","renderer"].includes(lock.kind));
  const localTime=scene?Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))):0;
  const terminal=scene?{...scene,camera:{...scene.camera,
    center_x:draft.end_center_x,center_y:draft.end_center_y}}:null;
  const problem=(()=>{
    if(!terminal)return "Select a Motion Canvas scene with original objects.";
    if(parseIssue)return parseIssue;
    try{validateCameraReframe(draft,terminal);return null;}catch(e){return message(e);}
  })();
  const authorityIssue=active&&scene?cameraSessionIssue(active,project,scene,stored):null;
  const canApply=!!scene&&!!active&&!problem&&!authorityIssue&&!locked&&!busy&&!pending&&!backupWarning&&!inFlight.current;
  const offset=cameraPreviewOffset(draft,localTime);
  const preview=!parseIssue?scene?.nodes.map(node=>({...node,x:node.x+offset.x,y:node.y+offset.y}))??[]:[];
  const background=project.visual_language.palette.find(t=>
    ["surface","background"].includes(t.name.toLowerCase()))?.value??"#0F1216";

  function backup(next:CameraDraft):boolean{
    try{
      lastVerifiedRaw.current=writeCameraCopy(window.localStorage,window.sessionStorage,
        next,writer.current,lastVerifiedRaw.current);
      setBackupWarning(null);
      setCopies([]);
      return true;
    }catch(reason){
      setBackupWarning("Camera recovery is not verified; no Project mutation will be sent. "+message(reason));
      return false;
    }
  }
  // A tab's writer pointer is required for automatic recovery; copies without
  // one remain available only after the author explicitly chooses Restore.
  useEffect(()=>{
    if(!scene||!scope){setAuthor(null);setCopies([]);return;}
    setError(null);
    try{
      const recovered=loadCameraCopy(window.localStorage,window.sessionStorage,scope);
      if(recovered){
        writer.current=recovered.writerId;
        lastVerifiedRaw.current=window.localStorage.getItem(cameraRecoveryKey(scope,recovered.writerId));
        setAuthor(reconcileCameraDraft(recovered.draft,project,scene));
        setCopies([]);
        setBackupWarning(null);
      }else{
        writer.current=crypto.randomUUID();lastVerifiedRaw.current=null;
        setAuthor(createCameraDraft(project,scene,stored??newDraft(scene)));
        const available=listCameraCopies(window.localStorage,scope);
        setCopies(available.slice(0,8));
        setBackupWarning(available.length
          ?"Saved Camera copies exist without this window's pointer. Choose Restore explicitly before authoring.":null);
      }
    }catch(reason){
      setAuthor(createCameraDraft(project,scene,stored??newDraft(scene)));
      setBackupWarning("Camera recovery cannot be read automatically. "+message(reason));
      setCopies(listCameraCopies(window.localStorage,scope).slice(0,8));
    }
    // Scope changes (not ordinary Project revisions) switch author identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.id,project.generation,project.active_branch,scene?.id]);
  useEffect(()=>{
    if(!scene||!active||!active.awaiting)return;
    const next=reconcileCameraDraft(active,project,scene);
    if(next!==active){
      setAuthor(next);
      backup(next);
    }
    // A canonical Project snapshot, rather than the timing of a transport
    // Promise, decides whether a submitted Camera change was acknowledged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.revision,stored,scene?.id,active?.awaiting?.id,active?.sequence]);

  const patch=(field:CameraField,raw:string)=>{
    if(!active)return;
    try{
      const next=editCameraDraft(active,field,raw);
      setAuthor(next);
      backup(next);
      setError(null);
    }catch(reason){setError(message(reason));}
  };
  const reload=()=>{
    if(!scene||!active||active.awaiting)return;
    const next=createCameraDraft(project,scene,stored??newDraft(scene));
    setAuthor(next);backup(next);setError(null);
  };
  const rebase=()=>{
    if(!scene||!active||active.awaiting)return;
    try{
      const next=rebaseCameraDraft(active,project,scene);
      setAuthor(next);backup(next);setError(null);
    }catch(reason){setError(message(reason));}
  };
  const confirmAbsent=async()=>{
    if(!scene||!active||pending||inFlight.current)return;
    setPending(true);
    try{
      // A real Project read is mandatory. Stale in-memory props alone cannot
      // prove a missing transport ACK was not applied to SQLite.
      const fresh=await bootstrap(project.id);
      const observed=fresh.project;
      if(!observed||observed.id!==project.id||observed.generation!==project.generation||
        observed.active_branch!==project.active_branch)
        throw new Error("Camera readback did not identify this exact Project.");
      const savedScene=observed.scenes.find(item=>item.id===scene.id);
      if(!savedScene)throw new Error("The saved Camera scene no longer exists.");
      const next=confirmCameraOutcomeAbsent(active,observed,savedScene);
      setAuthor(next);backup(next);setError(null);
    }catch(reason){setError(message(reason)+" Your Camera submission remains unconfirmed.");}
    finally{setPending(false);}
  };
  const restore=(id:string)=>{
    if(!scene||!scope||inFlight.current)return;
    try{
      const raw=window.localStorage.getItem(cameraRecoveryKey(scope,id));
      if(!raw)throw new Error("The selected Camera copy no longer exists.");
      const recovered=decodeCameraCopy(raw,scope,id);
      // Preserve original bytes; deliberate restoration creates an independent
      // writer rather than overwriting the old tab's immutable authored copy.
      writer.current=crypto.randomUUID();lastVerifiedRaw.current=null;
      window.sessionStorage.removeItem("motionwright.camera-writer.v1:"+encodeURIComponent(scope));
      const next=reconcileCameraDraft(recovered,project,scene);
      setAuthor(next);
      backup(next);
    }catch(reason){setError(message(reason));}
  };
  async function submit(operation:"upsert"|"detach"){
    if(!scene||!active||inFlight.current||busy||pending||backupWarning)return;
    if(operation==="upsert"&&!canApply)return;
    if(operation==="detach"&&(!stored||stale||locked||active.awaiting||cameraDraftChanged(active)))return;
    let attempt:CameraDraft;
    try{
      attempt=armCameraDraft(active,project,scene,operation,
        operation==="upsert"?cameraDraftValue(active):null,crypto.randomUUID());
    }catch(reason){setError(message(reason));return;}
    if(!backup(attempt))return;
    setAuthor(attempt);inFlight.current=true;setPending(true);setError(null);
    try{
      if(operation==="upsert") await commit({type:"upsert_camera_reframe",reframe:cameraDraftValue(attempt)});
      else await commit({type:"detach_camera_reframe",id:stored!.id});
      // Project revision readback must corroborate this ACK. The pending
      // record remains persisted until reconciled against canonical source.
    }catch(reason){
      setError(message(reason)+" The Camera result is unconfirmed; no automatic retry. Reopen and review saved state.");
    }finally{inFlight.current=false;setPending(false);}
  }
  async function save(){await submit("upsert");}
  async function detach(){await submit("detach");}
  return <div className="production-split">
    <section className="production-inspector" aria-label="Planar camera controls">
      <div className="production-section-title"><div><h2>Camera Reframe</h2>
        <p>Bounded original 2D camera motion · exact native Film</p></div></div>
      <p className="production-help">
        Author one genuine 2D camera-center translation through canonical
        editable subjects. This does not change focal length, create optical
        dolly or provide a 3D camera rig. Both endpoints must preserve every
        Canvas object inside the native safe area. Scenes with their own
        keyframes are rejected rather than silently overwriting motion.
      </p>
      {backupWarning && <div className="production-notice" role="alert" aria-label="Camera recovery warning">
        <strong>{backupWarning}</strong>
        <button type="button" className="secondary-button" disabled={!active}
          onClick={()=>{if(active)backup(active);}}>Verify local Camera copy</button>
      </div>}
      {copies.length>0 && <div className="production-notice" aria-label="Local Camera recovery copies">
        <strong>Existing Camera author copies require explicit Restore.</strong>
        {copies.map(id=><button key={id} type="button" className="secondary-button"
          onClick={()=>restore(id)}>Restore Camera copy {id}</button>)}
      </div>}
      {active?.awaiting && <div className="production-notice" role="region"
        aria-label="Unconfirmed Camera submission">
        <strong>Camera {active.awaiting.operation} from revision {active.awaiting.revision} is unconfirmed.</strong>
        <span>Review the saved Project after a reload. The earlier command cannot be silently replayed.</span>
        <button type="button" className="secondary-button" disabled={pending}
          onClick={confirmAbsent}>I verified this operation was not saved</button>
      </div>}
      {stale && <div className="production-notice" role="status">
        <strong>Draft from revision {active?.baseRevision ?? project.revision}; current revision {project.revision}.</strong>
        <span>Authored values remain intact until you explicitly reload.</span>
        <button type="button" className="secondary-button" disabled={!!active?.awaiting}
          onClick={rebase}>Keep authored input on current revision</button>
        <button type="button" className="secondary-button" disabled={!!active?.awaiting}
          onClick={reload}><RefreshCw size={14}/> Discard input and load saved Camera</button>
      </div>}
      <div className="production-plan-grid">
        {([
          ["start_center_x","Start center X"],
          ["start_center_y","Start center Y"],
          ["end_center_x","End center X"],
          ["end_center_y","End center Y"],
        ] as const).map(([key,label])=><label className="production-field" key={key}>
          <span>{label} · source pixels</span>
          <input aria-label={label} type="text" inputMode="decimal" role="spinbutton"
            aria-valuemin={key.endsWith("_x")?720:320} aria-valuemax={key.endsWith("_x")?1200:760}
            value={fields[key]} disabled={!active||pending}
            onChange={e=>patch(key,e.target.value)}/>
        </label>)}
      </div>
      <label className="production-field"><span>Duration · 30fps frames</span>
        <input aria-label="Camera duration frames" type="text" inputMode="numeric" role="spinbutton"
          aria-valuemin={12} aria-valuemax={180} value={fields.duration_frames}
          disabled={!active||pending} onChange={e=>patch("duration_frames",e.target.value)}/>
        <small>12–180 frames; hold final camera position for the remaining scene.
          Other frame rates require separate native acceptance.</small>
      </label>
      <p className="production-help">({draft.start_center_x}, {draft.start_center_y}) to
        ({draft.end_center_x}, {draft.end_center_y}), over
        {(draft.duration_frames/30).toFixed(3)} s. At most 80 pixels per axis.
      </p>
      {problem && <p role="status" className="production-error">{problem}</p>}
      {error && <p role="alert" className="production-error">{error}</p>}
      <button className="primary-button" disabled={!canApply} onClick={()=>void save()}>
        <Save size={14}/>{stored?"Update camera reframe":"Attach camera reframe"}
      </button>
      {active && cameraDraftChanged(active) && !active.awaiting && !stale &&
        <button type="button" className="text-button" disabled={pending}
          onClick={reload}>Discard this Camera author draft</button>}
      {stored && <div className="production-button-row">
        <button className="secondary-button" onClick={onOpenCanvas}>
          <ArrowUpRight size={14}/> Inspect editable objects
        </button>
        <button className="text-button" disabled={busy||stale||locked||pending||!!active?.awaiting||
          (!!active&&cameraDraftChanged(active))||!!backupWarning} onClick={()=>void detach()}>
          Detach, preserve final camera
        </button>
      </div>}
    </section>
    <section className="production-viewer" aria-label="Camera reframe editorial viewer">
      <div className="production-viewer-toolbar"><div><strong>Safe-area composition study</strong>
        <span>1920 × 1080 original Canvas · editorial preview only</span></div></div>
      <div className="production-preview-mat">
        {preview.length?<CompositionStudy nodes={preview} width={1920} height={1080}
          time={localTime} background={background} ink={inheritedTextInk(project.visual_language.palette)} textOverrides={displayProfile?.text_overrides}
          label={"Planar camera at "+localTime.toFixed(3)+" seconds, selected output profile editorial study"}/>:
          <p role="status">Add a semantic Canvas object to preview the move.</p>}
      </div>
      <div className="production-transport">
        <span className="mono">{localTime.toFixed(3)} s</span>
        <input aria-label="Camera reframe playhead" type="range"
          min={0} max={scene?seconds(scene.duration):6} step={1/30}
          value={localTime} disabled={!scene}
          onChange={e=>onSeek(seconds(scene!.start)+Number(e.target.value))}/>
      </div>
      <div className="production-evidence-note">
        <strong>Editorial view · not a native renderer frame</strong>
        <p>Semwright Film tests verify native temporal spans, original subjects,
          and safe areas. Optical dolly, 3D arc, focus tracking and stage
          lighting remain separate requirements.</p>
      </div>
    </section>
  </div>;
}
