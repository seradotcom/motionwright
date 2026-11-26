import {useEffect,useRef,useState} from "react";
import {readSavedNativeMasterInventory} from "./api";
import NativeAvReview from "./NativeAvReview";
import SavedMasterDelivery from "./SavedMasterDelivery";
import type {DeliveryProjectState} from "./deliveryProfileDraft";
import {createNativeAvReadGuard} from "./nativeAvAudioQc";
import type {SavedMasterHint,SavedMasterInventory} from "./savedNativeMedia";
import type {Project} from "./types";

type Discovery={kind:"idle"}|{kind:"loading"}|{kind:"error"}|{kind:"ready";inventory:SavedMasterInventory};

export default function SavedNativeMasters({project,deliverableId,blocked=false,currentProjectState}:{
  project:Project;deliverableId:string;blocked?:boolean;currentProjectState?:()=>DeliveryProjectState;
}){
  const guard=useRef(createNativeAvReadGuard());
  const readKey=guard.current.viewKey(JSON.stringify([project.id,project.generation,project.active_branch,project.revision,deliverableId]));
  const visible=useRef(readKey);visible.current=readKey;
  const active=useRef<{scope:string}|null>(null);
  const [stored,setStored]=useState<{key:string;state:Discovery}|null>(null);
  const [selected,setSelected]=useState<{key:string;hint:SavedMasterHint}|null>(null);
  const state=stored?.key===readKey?stored.state:{kind:"idle" as const};
  const inventory=state.kind==="ready"?state.inventory:null;
  const hint=selected?.key===readKey&&inventory?.items.some(item=>item.mux_receipt_id===selected.hint.mux_receipt_id
    &&item.master_sha256===selected.hint.master_sha256)?selected.hint:null;
  useEffect(()=>()=>{if(active.current)guard.current.cancel(active.current);},[readKey]);
  async function discover(){
    const ticket=guard.current.begin(readKey);active.current=ticket;
    setSelected(null);setStored({key:readKey,state:{kind:"loading"}});
    try{
      const inventory=await readSavedNativeMasterInventory(project,deliverableId);
      if(guard.current.isCurrent(ticket,visible.current))setStored({key:readKey,state:{kind:"ready",inventory}});
    }catch{
      if(guard.current.isCurrent(ticket,visible.current))setStored({key:readKey,state:{kind:"error"}});
    }
  }
  return <section className="caption-export-panel" aria-label="Saved original MP4s">
    <header><div><strong>Saved original MP4s</strong>
      <span>Reopen a completed master for this saved project and output profile.</span></div></header>
    <button type="button" className="button compact" disabled={state.kind==="loading"} onClick={()=>void discover()}>
      Find saved MP4s
    </button>
    {state.kind==="loading"&&<p role="status">Finding completed original masters...</p>}
    {state.kind==="error"&&<p role="alert">Saved originals are unavailable for this source and owner connection. Your project is unchanged.</p>}
    {inventory&&<>
      {inventory.items.length===0&&<p role="status">No saved MP4 with recovery information is available for this profile.</p>}
      <ul>{inventory.items.map(item=><li key={item.mux_receipt_id}>
        <span>{(item.frame_count*item.fps_den/item.fps_num).toFixed(2)} s · <code>{item.master_sha256}</code></span>
        <button type="button" className="button compact" aria-label={"Review saved original "+item.mux_receipt_id}
          onClick={()=>setSelected({key:readKey,hint:item})}>Review saved original</button>
      </li>)}</ul>
      {inventory.truncated&&<p role="status">Showing the 32 most recent original masters for this profile.</p>}
    </>}
    <p className="delivery-truth-note">Reopening verifies the recorded original and reads its MP4.
      Audio measurements are optional for review. Export uses a separate fresh destination permission and rechecks the saved profile.</p>
    {hint&&<NativeAvReview project={project} muxReceiptHint={hint} deliverableId={deliverableId}
      masterSha256={hint.master_sha256} embedded/>}
    {hint&&currentProjectState&&<SavedMasterDelivery key={JSON.stringify([readKey,hint.mux_receipt_id,hint.master_sha256])}
      project={project} deliverableId={deliverableId} hint={hint} blocked={blocked}
      currentProjectState={currentProjectState}/>}
  </section>;
}
