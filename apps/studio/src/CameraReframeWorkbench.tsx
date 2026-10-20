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
  acknowledgeCameraDraft, armCameraDraft, cameraAcknowledged, cameraDraftChanged, cameraDraftValue,
  cameraFields, cameraPointerKey, cameraRecoveryKey, cameraScope, cameraSessionIssue,
  createCameraDraft, decodeCameraCopy,
  editCameraDraft, listCameraCopies, loadCameraCopy,
  rebaseCameraDraft, reconcileCameraDraft, writeCameraConfirmationCopy, writeCameraCopy,
} from "./cameraReframeDraft";
import type { CameraDraft, CameraField } from "./cameraReframeDraft";
type Props={project:Project;scene:Scene|null;busy:boolean;displayProfile?:Project["deliverables"][number]|null;
  commit?:(change:Change)=>Promise<void>;
  commitCamera?:(change:Change)=>Promise<Project>;playhead:number;
  onSeek:(time:number)=>void;onOpenCanvas:()=>void;};
type CameraView={scope:string;epoch:number;writerId:string};
type CameraSubmission=CameraView&{attemptId:string;armedRaw:string};
type CameraInspection=CameraView&{id:string;attemptId:string};
type CameraCopyOwner=CameraView&{draft:CameraDraft;raw:string|null};

// This function is called only with the result of the original required
// mutation. Ordinary Project reads and recovery never acknowledge a command.
function persistCameraAcknowledgment(ticket:CameraSubmission,committed:Project,
  owner:CameraCopyOwner|null){
  const local=window.localStorage,session=window.sessionStorage;
  const pointer=cameraPointerKey(ticket.scope),key=cameraRecoveryKey(ticket.scope,ticket.writerId);
  if(session.getItem(pointer)!==ticket.writerId)
    throw new Error("The Camera recovery writer changed; its copy was preserved.");
  const live=owner&&owner.scope===ticket.scope&&owner.writerId===ticket.writerId?owner:null;
  if(live&&live.draft.awaiting?.id!==ticket.attemptId)
    throw new Error("A different Camera submission owns the visible draft.");
  const expected=live?live.raw:ticket.armedRaw;
  const raw=local.getItem(key);
  if(expected===null||raw!==expected)
    throw new Error("The Camera recovery copy changed; its latest bytes were preserved.");
  const durable=decodeCameraCopy(raw,ticket.scope,ticket.writerId);
  const armed=decodeCameraCopy(ticket.armedRaw,ticket.scope,ticket.writerId);
  if(armed.awaiting?.id!==ticket.attemptId||
    JSON.stringify(durable.awaiting)!==JSON.stringify(armed.awaiting))
    throw new Error("The retained Camera submission no longer matches the original command.");
  const source=live?.draft??durable;
  if(source.scope!==ticket.scope||source.sequence<durable.sequence||
    JSON.stringify(source.awaiting)!==JSON.stringify(armed.awaiting))
    throw new Error("Newer Camera input was preserved without changing its submission.");
  const committedScene=committed.scenes.find(item=>item.id===source.sceneId);
  if(!committedScene||cameraScope(committed,committedScene)!==ticket.scope)
    throw new Error("The Camera response belongs to another Project or scene.");
  const next=acknowledgeCameraDraft(source,ticket.attemptId,committed,committedScene);
  const nextWriterId=crypto.randomUUID();
  const nextRaw=writeCameraConfirmationCopy(local,session,next,ticket.writerId,raw,nextWriterId);
  return {next,raw:nextRaw,writerId:nextWriterId,owner:live};
}
function newDraft(scene:Scene|null):CameraReframe{
  const center=scene?.camera??{center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  return {id:crypto.randomUUID(),scene_id:scene?.id??crypto.randomUUID(),
    component_version:CAMERA_REFRAME_VERSION,start_center_x:center.center_x,
    start_center_y:center.center_y,end_center_x:center.center_x+12,
    end_center_y:center.center_y+4,duration_frames:90};
}
function message(error:unknown):string{return error instanceof Error?error.message:String(error);}
export default function CameraReframeWorkbench({project,scene,busy,commitCamera,playhead,onSeek,onOpenCanvas,displayProfile}:Props){
  const stored=project.production_design?.camera_reframes?.find(reframe=>reframe.scene_id===scene?.id);
  const scope=scene?cameraScope(project,scene):null;
  const writer=useRef<string>(crypto.randomUUID());
  const lastVerifiedRaw=useRef<string|null>(null);
  const [author,setAuthorState]=useState<CameraDraft|null>(()=>{
    if(!scene)return null;
    return createCameraDraft(project,scene,stored??newDraft(scene));
  });
  const authorRef=useRef(author);
  const setAuthor=(next:CameraDraft|null)=>{authorRef.current=next;setAuthorState(next);};
  const mounted=useRef(false);
  const view=useRef<{scope:string|null;epoch:number}>({scope,epoch:0});
  // Scope is invalidated during render, before a pending Promise can publish
  // through a passive effect left over from the previous selected scene.
  if(view.current.scope!==scope)view.current={scope,epoch:view.current.epoch+1};
  const [error,setError]=useState<string|null>(null);
  const [observation,setObservation]=useState<string|null>(null);
  const [backupWarning,setBackupWarning]=useState<string|null>(null);
  const [copies,setCopies]=useState<string[]>([]);
  const [pending,setPending]=useState(false);
  const inFlight=useRef<CameraSubmission|null>(null);
  const inspection=useRef<CameraInspection|null>(null);
  const [inspecting,setInspecting]=useState<CameraInspection|null>(null);
  const confirmPending=!!inspecting&&inspecting.scope===scope&&inspecting.epoch===view.current.epoch;
  const ownsView=(token:CameraView)=>mounted.current&&view.current.scope===token.scope&&
    view.current.epoch===token.epoch&&writer.current===token.writerId&&authorRef.current?.scope===token.scope;
  const ownsInspection=(token:CameraInspection)=>inspection.current===token&&ownsView(token)&&
    authorRef.current?.awaiting?.id===token.attemptId;
  const visibleCopyOwner=():CameraCopyOwner|null=>{
    const current=authorRef.current;
    if(!mounted.current||!current||current.scope!==view.current.scope)return null;
    return {scope:current.scope,epoch:view.current.epoch,writerId:writer.current,
      draft:current,raw:lastVerifiedRaw.current};
  };
  useEffect(()=>{
    mounted.current=true;
    return ()=>{
      mounted.current=false;
      view.current={...view.current,epoch:view.current.epoch+1};
      // Cleanup invalidates view publication, never the durable operation.
    };
  },[]);
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
  const canApply=!!commitCamera&&!!scene&&!!active&&!problem&&!authorityIssue&&!locked&&!busy&&!pending&&!backupWarning&&!inFlight.current;
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
    setObservation(null);
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
  // A Project publication can come from another writer, including identical
  // Camera content or absence. It never clears the retained original command.

  const patch=(field:CameraField,raw:string)=>{
    const current=authorRef.current;
    if(!current||current.scope!==scope)return;
    try{
      const next=editCameraDraft(current,field,raw);
      setAuthor(next);
      backup(next);
      setError(null);
    }catch(reason){setError(message(reason));}
  };
  const reload=()=>{
    const current=authorRef.current;
    if(!scene||!current||current.scope!==scope||current.awaiting||inFlight.current)return;
    const next=createCameraDraft(project,scene,stored??newDraft(scene));
    setAuthor(next);backup(next);setError(null);
  };
  const rebase=()=>{
    const current=authorRef.current;
    if(!scene||!current||current.scope!==scope||current.awaiting||inFlight.current)return;
    try{
      const next=rebaseCameraDraft(current,project,scene);
      setAuthor(next);backup(next);setError(null);
    }catch(reason){setError(message(reason));}
  };
  const inspectSaved=async()=>{
    const current=authorRef.current;
    if(!scene||!scope||!current?.awaiting||current.scope!==scope||confirmPending||
      (inspection.current&&ownsInspection(inspection.current)))return;
    const token:CameraInspection={scope,epoch:view.current.epoch,writerId:writer.current,
      id:crypto.randomUUID(),attemptId:current.awaiting.id};
    inspection.current=token;setInspecting(token);setObservation(null);
    try{
      // Inspection observes saved state only. Even an unchanged revision
      // cannot prove that a held original request will never commit later.
      const fresh=await bootstrap(project.id);
      if(!ownsInspection(token))return;
      const observed=fresh.project;
      const savedScene=observed?.scenes.find(item=>item.id===current.sceneId);
      if(!observed||!savedScene||cameraScope(observed,savedScene)!==token.scope)
        throw new Error("Camera inspection did not identify this exact Project and scene.");
      setObservation("Saved Project revision "+observed.revision+" was inspected. This read does not identify "
        +"the original request's outcome; the Camera submission remains unconfirmed.");
    }catch(reason){
      if(ownsInspection(token))setObservation(message(reason)+" The Camera submission remains unconfirmed.");
    }finally{
      if(inspection.current===token){
        inspection.current=null;
        if(mounted.current)setInspecting(current=>current===token?null:current);
      }
    }
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
    const send=commitCamera;
    // A missing required callback is not permission to use a UI-only or
    // error-swallowing commit. Do not arm or persist a submission in that case.
    if(!send){setError("Camera saving is currently unavailable. Your local input is preserved.");return;}
    const current=authorRef.current;
    if(!scene||!current||current.scope!==scope||inFlight.current||busy||pending||backupWarning)return;
    if(operation==="upsert"&&!canApply)return;
    if(operation==="detach"&&(!stored||stale||locked||current.awaiting||cameraDraftChanged(current)))return;
    let attempt:CameraDraft;
    try{
      const value=operation==="upsert"?cameraDraftValue(current):null;
      if(value)validateCameraReframe(value,{...scene,camera:{...scene.camera,
        center_x:value.end_center_x,center_y:value.end_center_y}});
      attempt=armCameraDraft(current,project,scene,operation,value,crypto.randomUUID());
    }catch(reason){setError(message(reason));return;}
    if(!backup(attempt))return;
    const ticket:CameraSubmission={scope:attempt.scope,epoch:view.current.epoch,writerId:writer.current,
      attemptId:attempt.awaiting!.id,armedRaw:lastVerifiedRaw.current!};
    setAuthor(attempt);inFlight.current=ticket;setPending(true);setError(null);setObservation(null);
    try{
      const committed=operation==="upsert"
        ?await send({type:"upsert_camera_reframe",reframe:cameraDraftValue(attempt)})
        :await send({type:"detach_camera_reframe",id:stored!.id});
      const committedScene=committed?.scenes?.find(item=>item.id===attempt.sceneId);
      if(!committedScene||!cameraAcknowledged(attempt,committed,committedScene,ticket.attemptId))
        throw new Error("The original Camera response did not confirm this submitted operation.");
      try{
        const result=persistCameraAcknowledgment(ticket,committed,visibleCopyOwner());
        const owner=result.owner;
        if(owner&&ownsView(owner)&&authorRef.current===owner.draft&&lastVerifiedRaw.current===owner.raw){
          writer.current=result.writerId;lastVerifiedRaw.current=result.raw;
          setAuthor(result.next);setBackupWarning(null);setCopies([]);setError(null);setObservation(null);
        }
      }catch(reason){
        // The mutation succeeded, but a failed or superseded recovery write
        // must not clear the local awaiting marker or authorize a retry.
        if(ownsView(ticket))setError("The Camera command was saved, but its local confirmation could not be retained. "
          +message(reason)+" No automatic retry was sent.");
      }
    }catch(reason){
      if(ownsView(ticket))setError(message(reason)+" The Camera result is unconfirmed; no automatic retry. Inspect the saved state.");
    }finally{
      if(inFlight.current===ticket){inFlight.current=null;if(mounted.current)setPending(false);}
    }
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
      {!commitCamera&&<p role="status" className="production-error">
        Camera saving is currently unavailable. Your local input is preserved.
      </p>}
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
        <span>The earlier command is retained. Inspecting the Project does not cancel it or permit another attempt.</span>
        <button type="button" className="secondary-button" disabled={confirmPending}
          onClick={inspectSaved}>{confirmPending?"Inspecting saved Camera…":"Inspect saved Camera"}</button>
        {observation&&<span role="status">{observation}</span>}
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
        <button className="text-button" disabled={!commitCamera||busy||stale||locked||pending||!!active?.awaiting||
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
