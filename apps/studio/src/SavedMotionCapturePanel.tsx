import {useEffect,useRef,useState} from "react";
import {readSavedMotionCaptureInventory} from "./api";
import {createNativeAvReadGuard} from "./nativeAvAudioQc";
import type {SavedMotionCaptureHint,SavedMotionCaptureInventory} from "./savedMotionCapture";
import type {Project} from "./types";

type Discovery={kind:"idle"}|{kind:"loading"}|{kind:"error"}|{kind:"ready";inventory:SavedMotionCaptureInventory};
export default function SavedMotionCapture({project,deliverableId,blocked,onRecover}:{
  project:Project;deliverableId:string;blocked:boolean;onRecover(hint:SavedMotionCaptureHint):void;
}){
  const guard=useRef(createNativeAvReadGuard());
  const key=guard.current.viewKey(JSON.stringify([project.id,project.generation,project.active_branch,project.revision,deliverableId,blocked]));
  const visible=useRef(key);visible.current=key;
  const active=useRef<{scope:string}|null>(null);
  const [stored,setStored]=useState<{key:string;state:Discovery}|null>(null);
  const state=stored?.key===key?stored.state:{kind:"idle" as const};
  const capture=state.kind==="ready"?state.inventory.capture:null;
  useEffect(()=>()=>{if(active.current)guard.current.cancel(active.current);},[key]);
  async function discover(){
    if(blocked)return;
    const ticket=guard.current.begin(key);active.current=ticket;
    setStored({key,state:{kind:"loading"}});
    try{
      const inventory=await readSavedMotionCaptureInventory(project,deliverableId);
      if(guard.current.isCurrent(ticket,visible.current))setStored({key,state:{kind:"ready",inventory}});
    }catch{
      if(guard.current.isCurrent(ticket,visible.current))setStored({key,state:{kind:"error"}});
    }
  }
  return <section className="caption-export-panel" aria-label="Saved Motion Canvas capture">
    <header><div><strong>Saved Motion Canvas capture</strong>
      <span>Continue from the complete capture saved for this project and output profile.</span></div></header>
    <div className="delivery-editor-actions">
      <button type="button" className="button compact" disabled={blocked||state.kind==="loading"} onClick={()=>void discover()}>
        Find saved capture
      </button>
      {capture&&<button type="button" className="button compact" disabled={blocked} onClick={()=>onRecover(capture)}>
        Reopen saved capture
      </button>}
    </div>
    {state.kind==="loading"&&<p role="status">Reading the saved capture history...</p>}
    {state.kind==="error"&&<p role="alert">The original capture is unavailable for this source and owner connection. Existing files and history are unchanged.</p>}
    {state.kind==="ready"&&!capture&&<p role="status">No complete capture with recovery information is available for this saved profile.</p>}
    {capture&&<p role="status">{capture.segment_count} segment{capture.segment_count===1?"":"s"} · {capture.frame_count} frames · {(capture.frame_count*capture.fps_den/capture.fps_num).toFixed(2)} s saved.</p>}
    <p className="delivery-truth-note">Reopening checks the original files and restores their preview.
      You can then continue to the MP4 controls below. Rendering is not repeated; delivery still requires its current permissions and remaining budget.</p>
  </section>;
}
