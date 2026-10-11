import { useEffect, useState } from "react";
import { ArrowUpRight, RefreshCw, Save } from "lucide-react";
import type { Change, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { CAMERA_REFRAME_VERSION, cameraPreviewOffset, validateCameraReframe } from "./cameraReframe";
import type { CameraReframe } from "./cameraReframe";
type Props={project:Project;scene:Scene|null;busy:boolean;
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
export default function CameraReframeWorkbench({project,scene,busy,commit,playhead,onSeek,onOpenCanvas}:Props){
  const stored=project.production_design?.camera_reframes?.find(reframe=>reframe.scene_id===scene?.id);
  const [draft,setDraft]=useState<CameraReframe>(()=>structuredClone(stored??newDraft(scene)));
  const [base,setBase]=useState(project.revision);
  const [error,setError]=useState<string|null>(null);
  const stale=base!==project.revision;
  const locked=project.locks.some(lock=>
    ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource) &&
    ["content","position","timing","renderer"].includes(lock.kind));
  const localTime=scene?Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))):0;
  const terminal=scene?{...scene,camera:{...scene.camera,
    center_x:draft.end_center_x,center_y:draft.end_center_y}}:null;
  const problem=(()=>{
    if(!terminal)return "Select a Motion Canvas scene with original objects.";
    try{validateCameraReframe(draft,terminal);return null;}catch(e){return message(e);}
  })();
  const canApply=!!scene && !problem && !stale && !locked && !busy;
  const offset=cameraPreviewOffset(draft,localTime);
  const preview=scene?.nodes.map(node=>({...node,x:node.x+offset.x,y:node.y+offset.y}))??[];
  const background=project.visual_language.palette.find(t=>
    ["surface","background"].includes(t.name.toLowerCase()))?.value??"#0F1216";
  const reload=()=>{setDraft(structuredClone(stored??newDraft(scene)));setBase(project.revision);setError(null);};
  useEffect(()=>{
    setDraft(structuredClone(stored??newDraft(scene)));
    setBase(project.revision);setError(null);
    // Preserve unsaved work on same-scene revisions until an explicit reload.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation,scene?.id]);
  useEffect(()=>{
    if(stored && JSON.stringify(stored)===JSON.stringify(draft))setBase(project.revision);
  },[stored,draft,project.revision]);
  const patch=<K extends keyof CameraReframe>(key:K,value:CameraReframe[K])=>
    setDraft(old=>({...old,[key]:value}));
  async function save(){
    if(!canApply)return;
    setError(null);
    try{await commit({type:"upsert_camera_reframe",reframe:draft});setBase(project.revision+1);}
    catch(e){setError(message(e));}
  }
  async function detach(){
    if(!stored||stale||busy||locked)return;
    setError(null);
    try{await commit({type:"detach_camera_reframe",id:stored.id});setBase(project.revision+1);}
    catch(e){setError(message(e));}
  }
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
      {stale && <div className="production-notice" role="status">
        <strong>Draft from revision {base}; current revision {project.revision}.</strong>
        <span>Authored values remain intact until you explicitly reload.</span>
        <button className="secondary-button" onClick={reload}>
          <RefreshCw size={14}/> Reload camera draft
        </button>
      </div>}
      <div className="production-plan-grid">
        {([
          ["start_center_x","Start center X"],
          ["start_center_y","Start center Y"],
          ["end_center_x","End center X"],
          ["end_center_y","End center Y"],
        ] as const).map(([key,label])=><label className="production-field" key={key}>
          <span>{label} · source pixels</span>
          <input aria-label={label} type="number" min={key.endsWith("_x")?720:320}
            max={key.endsWith("_x")?1200:760} step={1} value={draft[key]}
            onChange={e=>patch(key,Number(e.target.value))}/>
        </label>)}
      </div>
      <label className="production-field"><span>Duration · 30fps frames</span>
        <input aria-label="Camera duration frames" type="number"
          min={12} max={180} step={1} value={draft.duration_frames}
          onChange={e=>patch("duration_frames",Number(e.target.value))}/>
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
      {stored && <div className="production-button-row">
        <button className="secondary-button" onClick={onOpenCanvas}>
          <ArrowUpRight size={14}/> Inspect editable objects
        </button>
        <button className="text-button" disabled={busy||stale||locked} onClick={()=>void detach()}>
          Detach, preserve final camera
        </button>
      </div>}
    </section>
    <section className="production-viewer" aria-label="Camera reframe editorial viewer">
      <div className="production-viewer-toolbar"><div><strong>Safe-area composition study</strong>
        <span>1920 × 1080 original Canvas · editorial preview only</span></div></div>
      <div className="production-preview-mat">
        {preview.length?<CompositionStudy nodes={preview} width={1920} height={1080}
          time={localTime} background={background}
          label={"Planar camera at "+localTime.toFixed(3)+" seconds, original graphic"}/>:
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
