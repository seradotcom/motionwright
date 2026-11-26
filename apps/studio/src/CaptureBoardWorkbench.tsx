import {useEffect,useState} from "react";
import type {Change,CanvasNode,Project,Scene,DeliverableProfile} from "./types";
import type {CaptureBoardMode,CaptureBoardRaw,CaptureBoardRawEntry,CaptureBoardRole} from "./captureBoard";
import {applyCaptureBoardChange,captureBoardFromRaw,defaultCaptureBoardRaw,realizeCaptureBoard} from "./captureBoard";
import {workbenchScope,workbenchHasWork} from "./creativeWorkbenchDraft";
import {useCreativeWorkbenchDraft,WorkbenchDraftFeedback} from "./useCreativeWorkbenchDraft";
import CompositionStudy from "./CompositionStudy";
import {useCaptureSourceImages} from "./useCaptureSourceImages";
import {seconds} from "./types";

type Props={project:Project;scene:Scene|null;displayProfile:DeliverableProfile|null;commit:(change:Change)=>Promise<Project>;
  busy:boolean;playhead:number;onSeek:(time:number)=>void;onOpenRender:()=>void;preferredMode?:CaptureBoardMode};
const studyId="00000000-0000-4000-8000-000000000005";
export default function CaptureBoardWorkbench(props:Props){
  return <BoardEditor key={workbenchScope(props.project,{kind:"capture_board",sceneId:props.scene?.id??null})} {...props}/>;
}
function BoardEditor({project,scene,displayProfile,commit,busy,playhead,onSeek,onOpenRender,preferredMode}:Props){
  const stored=project.production_design?.capture_boards?.find(board=>board.scene_id===scene?.id);
  const editor=useCreativeWorkbenchDraft(project,{kind:"capture_board",sceneId:scene?.id??null},commit,busy);
  const input=editor.draft.input as {kind:"capture_board";config:CaptureBoardRaw},raw=input.config;
  const [preview,setPreview]=useState<CanvasNode[]>([]),[problem,setProblem]=useState<string|null>(null),[preparing,setPreparing]=useState(false);
  const [aspect,setAspect]=useState<"selected"|"landscape"|"square"|"portrait">("selected");
  const size=aspect==="landscape"?{width:1920,height:1080}:aspect==="square"?{width:1080,height:1080}:
    aspect==="portrait"?{width:1080,height:1920}:displayProfile??{width:1920,height:1080};
  const captures=project.production_design?.captures??[];
  const sourceImages=useCaptureSourceImages(project,stored?.id??studyId,raw.entries.map(entry=>entry.capture_id));
  useEffect(()=>{
    let active=true;
    setPreparing(true);setPreview([]);setProblem(null);
    Promise.resolve().then(async()=>{
      if(!scene)throw new Error("Choose a saved scene.");
      const board=captureBoardFromRaw(project,scene.id,stored?.id??studyId,raw);
      const candidate=structuredClone(project);
      await applyCaptureBoardChange(candidate,{type:"upsert_capture_board",board});
      return realizeCaptureBoard(project,board,size.width,size.height);
    }).then(nodes=>{if(active){setPreview(nodes);setPreparing(false);}},
      reason=>{if(active){setProblem(reason instanceof Error?reason.message:String(reason));setPreparing(false);}});
    return()=>{active=false;};
  },[project,scene,stored?.id,raw,size.width,size.height]);
  const edit=(config:CaptureBoardRaw)=>editor.edit({kind:"capture_board",config});
  const setMode=(mode:CaptureBoardMode)=>{
    const initial=defaultCaptureBoardRaw(mode);
    edit({...initial,entries:initial.entries.map((entry,i)=>({...raw.entries[i],role:entry.role})) as CaptureBoardRaw["entries"]});
  };
  const patch=(index:number,field:keyof CaptureBoardRawEntry,value:string)=>{
    const entries=structuredClone(raw.entries);
    // A source-use declaration belongs to the selected original capture.
    if(field==="capture_id"&&entries[index].capture_id!==value)entries[index].permission_note="";
    Object.assign(entries[index],{[field]:value});
    edit({...raw,entries});
  };
  const localTime=scene?Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))):0;
  const blocked=editor.blocked||preparing||!!problem||!preview.length||!scene;
  return <div className="production-split">
    <section className="production-inspector" aria-label="Source board authoring">
      <h2>{raw.mode==="reference_board"?"Reference board":"Product flow bridge"}</h2>
      <p className="production-help">Use two distinct registered first-party synthetic captures. The saved configuration binds all three source hashes and keeps source labels visible. The flow mode shows the first state, then reveals the second at an authored interval; it never claims an observed transition.</p>
      <WorkbenchDraftFeedback editor={editor} project={project}/>
      {preferredMode&&preferredMode!==raw.mode&&!stored&&!workbenchHasWork(editor.draft)&&
        <button type="button" className="secondary-button" disabled={editor.blocked} onClick={()=>setMode(preferredMode)}>Use {preferredMode==="product_flow_bridge"?"Product flow bridge":"Reference board"} mode</button>}
      <label className="production-field"><span>Source board mode</span><select aria-label="Source board mode" value={raw.mode} disabled={editor.busy}
        onChange={event=>setMode(event.target.value as CaptureBoardMode)}>
        <option value="reference_board">Static reference board</option><option value="product_flow_bridge">Editorial product-state sequence</option>
      </select></label>
      {!captures.length&&<p role="status">Import and register two original first-party captures in Capture sources first. No screenshot is synthesized here.</p>}
      {raw.entries.map((entry,index)=><fieldset key={index} className="production-inspector-group">
        <legend>{raw.mode==="product_flow_bridge"?(index===0?"Before state":"After state"):"Reference "+(index+1)}</legend>
        <label className="production-field"><span>Registered capture</span><select aria-label={"Board capture "+(index+1)} value={entry.capture_id} disabled={editor.busy}
          onChange={event=>patch(index,"capture_id",event.target.value)}>
          <option value="">Choose source</option>{captures.map(source=><option key={source.id} value={source.id}>
            r{source.source_revision} / {source.screenshot_sha256.slice(0,12)} / {source.app_version}
          </option>)}
        </select></label>
        <label className="production-field"><span>Narrative role</span><select aria-label={"Board role "+(index+1)} value={entry.role}
          disabled={editor.busy||raw.mode==="product_flow_bridge"} onChange={event=>patch(index,"role",event.target.value)}>
          {((raw.mode==="product_flow_bridge"?["before","after"]:["context","mechanism","evidence","comparison"]) as CaptureBoardRole[]).map(role=><option key={role} value={role}>{role}</option>)}
        </select></label>
        <label className="production-field"><span>Short authored label</span><input aria-label={"Board label "+(index+1)} maxLength={24} value={entry.label} disabled={editor.busy}
          onChange={event=>patch(index,"label",event.target.value)}/></label>
        <label className="production-field"><span>Explicit permission declaration</span><textarea aria-label={"Board source permission "+(index+1)} value={entry.permission_note} maxLength={320}
          disabled={editor.busy} onChange={event=>patch(index,"permission_note",event.target.value)}/></label>
        {captures.filter(source=>source.id===entry.capture_id).map(source=><p key={source.id} className="production-help">
          Original revision {source.source_revision}, scope {source.declared_scope}. PNG {source.screenshot_sha256}, manifest {source.manifest_sha256}, fixture {source.fixture_state_sha256}.
        </p>)}
      </fieldset>)}
      {raw.mode==="product_flow_bridge"&&<div className="production-plan-grid">
        <label className="production-field"><span>Second state reveal start (30fps)</span><input aria-label="Board reveal start frame" inputMode="numeric" value={raw.reveal_start_frame}
          disabled={editor.busy} onChange={event=>edit({...raw,reveal_start_frame:event.target.value})}/></label>
        <label className="production-field"><span>Reveal duration (frames)</span><input aria-label="Board reveal duration frames" inputMode="numeric" value={raw.reveal_duration_frames}
          disabled={editor.busy} onChange={event=>edit({...raw,reveal_duration_frames:event.target.value})}/></label>
      </div>}
      {problem&&<p className="production-error" role="status">{problem}</p>}
      <button type="button" className="primary-button" disabled={blocked} onClick={()=>void editor.send("save")}>{stored?"Update source board":"Add source board"}</button>
      {stored&&<button type="button" className="text-button" disabled={editor.blocked||editor.busy||workbenchHasWork(editor.draft)}
        onClick={()=>void editor.send("detach")}>Remove board and generated slots</button>}
      <p className="production-help">Removal preserves imported assets, capture registrations and claims. This bounded component uses a dedicated empty scene and its own stable layout; edit its configuration here, rather than replacing source slots individually. Other content and mandatory brand profiles are not replaced.</p>
    </section>
    <section className="production-viewer" aria-label="Source board layout study">
      <h3>Original source layout</h3>
      <label className="production-field"><span>Aspect study</span><select aria-label="Source board aspect" value={aspect} onChange={event=>setAspect(event.target.value as typeof aspect)}>
        <option value="selected">Selected output</option><option value="landscape">16:9</option><option value="square">1:1</option><option value="portrait">9:16</option>
      </select></label>
      <p className="production-help">Original PNGs are read from this project's saved sources. This layout study previews framing and timing; use Native Render to review the saved film.</p>
      {sourceImages.status==="loading"&&<p role="status">Checking original source images...</p>}
      {sourceImages.status==="ready"&&<p role="status">Original source images loaded from verified project assets.</p>}
      {sourceImages.error&&<div><p className="production-error" role="status">Original capture preview unavailable: {sourceImages.error}</p>
        <button type="button" className="secondary-button" onClick={sourceImages.retry}>Retry original images</button></div>}
      <div className="production-preview-mat">{preview.length?<CompositionStudy nodes={preview} width={size.width} height={size.height} time={localTime}
        imageSources={sourceImages.images} background="#101820"
        label={sourceImages.status==="ready"?"Source board with original capture images":"Source board layout, original images unavailable"}/>:<p role="status">{preparing?"Preparing source-bound layout...":"Choose two valid source records and review the constraints."}</p>}</div>
      <label className="production-field"><span>Scene-local time ({localTime.toFixed(2)} s)</span><input aria-label="Source board playhead" type="range" min={0} max={scene?seconds(scene.duration):6}
        step={1/30} value={localTime} disabled={!scene} onChange={event=>onSeek(seconds(scene!.start)+Number(event.target.value))}/></label>
      <button type="button" className="secondary-button" disabled={!stored||editor.blocked||workbenchHasWork(editor.draft)} onClick={onOpenRender}>Review saved board in Native Render</button>
    </section>
  </div>;
}
