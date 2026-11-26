import {useEffect,useRef,useState} from "react";
import {exportSavedNativeMaster} from "./api";
import {captureDeliveryDispatch,type DeliveryProjectState} from "./deliveryProfileDraft";
import type {SavedMasterHint} from "./savedNativeMedia";
import type {MasterExportReceipt,Project} from "./types";

export default function SavedMasterDelivery({project,deliverableId,hint,blocked,currentProjectState}:{
  project:Project;deliverableId:string;hint:SavedMasterHint;blocked:boolean;
  currentProjectState:()=>DeliveryProjectState;
}){
  const [destination,setDestination]=useState("");
  const [integrity,setIntegrity]=useState(false);
  const [busy,setBusy]=useState(false);
  const [receipt,setReceipt]=useState<MasterExportReceipt|null>(null);
  const [error,setError]=useState<string|null>(null);
  const serial=useRef(0);
  const mounted=useRef(true);
  const captioned=project.deliverables.find(p=>p.id===deliverableId)?.burn_in_captions!==false;
  const ready=!blocked&&!captioned&&destination.trim().toLowerCase().endsWith(".mp4");
  const key=JSON.stringify([hint.mux_receipt_id,hint.master_sha256,destination,integrity]);
  const live=useRef({project,deliverableId,blocked,ready,key,currentProjectState,serial:serial.current});
  live.current={project,deliverableId,blocked,ready,key,currentProjectState,serial:serial.current};
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  function changed(){serial.current+=1;setReceipt(null);setError(null);}
  async function deliver(){
    if(busy)return;
    let gesture:ReturnType<typeof captureDeliveryDispatch>|null=null;
    try{
      gesture=captureDeliveryDispatch({project,profileId:deliverableId,inputSerial:serial.current,actionKey:key},()=>{
        const now=live.current;
        return {currentProject:now.currentProjectState(),renderedProject:now.project,
          profileId:now.deliverableId,inputSerial:serial.current,renderedInputSerial:now.serial,
          actionKey:now.key,ready:now.ready,draftBlocked:now.blocked,mounted:mounted.current};
      });
      setBusy(true);setError(null);setReceipt(null);
      const result=await exportSavedNativeMaster(project,deliverableId,hint,destination,integrity,gesture.beforeDispatch);
      if(gesture.stillCurrent())setReceipt(result);
    }catch(cause){
      if(mounted.current&&(!gesture||gesture.stillCurrent()))setError(
        typeof cause==="string"?cause:cause instanceof Error?cause.message:
          "Saved MP4 delivery did not return a confirmed result. Inspect the destination before starting another copy.");
    }finally{if(mounted.current)setBusy(false);}
  }
  return <section aria-label="Deliver saved original MP4" className="caption-export-panel">
    <header><div><strong>Deliver this saved original</strong>
      <span>Verify the original media again and copy it to a new local destination.</span></div></header>
    <label className="caption-path"><span className="field-label">New absolute .mp4 destination</span>
      <input aria-label="Saved MP4 export path" value={destination}
        onChange={event=>{changed();setDestination(event.target.value);}}/></label>
    <label className="caption-toggle"><input type="checkbox" checked={integrity}
      aria-label="Write saved MP4 integrity receipt"
      onChange={event=>{changed();setIntegrity(event.target.checked);}}/>
      Include the portable SHA-256 receipt</label>
    <button type="button" className="button button-primary" disabled={!ready||busy} onClick={()=>void deliver()}>
      {busy?"Checking and copying...":"Export saved original MP4"}</button>
    {captioned&&<p role="status">This profile burns in captions. Export requires independent verification of the original decoded caption pixels on this device.</p>}
    {blocked&&<p role="status">Finish the current project operation and save or resolve delivery drafts before exporting.</p>}
    {error&&<p role="alert">{error}</p>}
    {receipt&&<div role="status" aria-label="Saved MP4 delivery receipt">
      <p>{receipt.source_current?"Exported and verified":"Exported historical original"}: {receipt.destination}</p>
      <p>{receipt.size_bytes} bytes · SHA-256 <code>{receipt.sha256}</code></p>
      {receipt.integrity_manifest_path&&<p>Integrity receipt: {receipt.integrity_manifest_path}</p>}
    </div>}
    <p className="delivery-truth-note">Each export needs fresh permission for its destination. Existing files are never overwritten.
      Saved audio targets must have their original passing measurement. This copies the original without rendering it again.</p>
  </section>;
}
