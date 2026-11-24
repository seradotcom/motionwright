import { useEffect, useRef, useState } from "react";
import type { Change, DeliverableProfile, DeliverySettingPropagation, Project } from "./types";
import type { DeliveryProjectState } from "./deliveryProfileDraft";
import { sameProjectRevision } from "./projectMutation";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import { buildDeliverySettingPropagation, deliveryFamilyRows, displayDeliverySetting,
  readDeliverySetting, SHARED_DELIVERY_FIELDS, type SharedDeliveryField } from "./deliveryFamily";

type Retained = { raw: string | null; error: string | null };
const custodyEvent="motionwright:delivery-family-custody";
const notifyCustody=()=>window.dispatchEvent(new Event(custodyEvent));
const recoveryKey=(project:Project)=>"motionwright.delivery-family-attempt.v1:"+JSON.stringify([project.id,project.generation,project.active_branch]);
function readAttempt(key:string):Retained{
  try{return {raw:window.localStorage.getItem(key),error:null};}
  catch(reason){return {raw:null,error:"Saved attempt custody is unavailable: "+String(reason)};}
}
function removeExactAttempt(key:string,raw:string){
  if(window.localStorage.getItem(key)!==raw)throw new Error("The retained attempt changed; inspect its saved copy.");
  window.localStorage.removeItem(key);
  if(window.localStorage.getItem(key)!==null)throw new Error("Unable to release the retained attempt.");
  notifyCustody();
}
function parseAttempt(raw:string,project:Project):DeliverySettingPropagation{
  if(raw.length>256_000)throw new Error("The retained attempt exceeds the review budget.");
  const value=JSON.parse(raw) as DeliverySettingPropagation;
  if(!value||value.project_id!==project.id||value.generation!==project.generation||value.branch_id!==project.active_branch
    ||!Number.isSafeInteger(value.expected_revision)||value.expected_revision<0||!Array.isArray(value.targets)
    ||!value.targets.length||value.targets.length>=128||!value.source||!SHARED_DELIVERY_FIELDS.some(f=>f.field===value.source.field))
    throw new Error("The retained attempt is malformed or has a different source scope.");
  // Recovery only reads this payload; it never dispatches it.
  return value;
}
export default function DeliveryFamilyWorkbench({project,source,blocked,currentProjectState,commit}:{
  project:Project;source:DeliverableProfile;blocked:boolean;
  currentProjectState:()=>DeliveryProjectState;commit:(change:Change)=>Promise<Project>;
}){
  const [field,setField]=useState<SharedDeliveryField>("brand_profile");
  const [selection,setSelection]=useState<string[]>([]);
  const [pending,setPending]=useState(false);
  const [failure,setFailure]=useState<string|null>(null);
  const key=recoveryKey(project);
  const [retained,setRetained]=useState(()=>readAttempt(key));
  const [inspection,setInspection]=useState<{raw:string;revision:number;matched:boolean}|null>(null);
  const sent=useRef(false);
  useEffect(()=>{
    const update=(event:StorageEvent)=>{if(event.key===key||event.key===null){setRetained(readAttempt(key));setInspection(null);}};
    const local=()=>{setRetained(readAttempt(key));setInspection(null);};
    window.addEventListener("storage",update);
    window.addEventListener(custodyEvent,local);
    local();
    return()=>{window.removeEventListener("storage",update);window.removeEventListener(custodyEvent,local);};
  },[key]);
  const rows=deliveryFamilyRows(project,source.id,field);
  if(!rows.length&&!retained.raw&&!retained.error)return null;
  const value=readDeliverySetting(source,field);
  const disabled=blocked||pending||sent.current||retained.raw!==null||!!retained.error;
  const submit=async()=>{
    if(disabled||!selection.length||sent.current)return;
    const current=currentProjectState();
    if(current.busy || !sameProjectRevision(current.project,project)){
      setFailure("The saved project changed. Inspect the current profile family again.");
      return;
    }
    let propagation:DeliverySettingPropagation,raw:string;
    try{
      propagation=buildDeliverySettingPropagation(project,source.id,field,selection);
      raw=JSON.stringify(propagation);
      // Retain the exact scope and old/new values before requesting a grant.
      // A failed local checkpoint must prevent dispatch.
      const previous=readAttempt(key);
      if(previous.raw!==null||previous.error)throw new Error("A prior family attempt still needs review.");
      window.localStorage.setItem(key,raw);
      if(window.localStorage.getItem(key)!==raw)throw new Error("Unable to retain the exact attempt before sending.");
      setRetained({raw,error:null});
      notifyCustody();
    }catch(reason){setRetained(readAttempt(key));setFailure(String(reason));return;}
    sent.current=true;setPending(true);setFailure(null);
    try{
      const receipt=await commit({type:"propagate_delivery_setting",propagation});
      if(receipt.id!==project.id||receipt.generation!==project.generation||receipt.active_branch!==project.active_branch
        ||receipt.revision!==project.revision+1||propagation.targets.some(row=>{
          const saved=receipt.deliverables.find(p=>p.id===row.profile_id);
          return !saved||!sameValue(readDeliverySetting(saved,field),propagation.source);
        })) throw new Error("The returned saved project does not establish the selected update.");
      removeExactAttempt(key,raw);
      setRetained(readAttempt(key));
    }catch(reason){
      setFailure("Update not confirmed. Inspect the saved revision and history before a new attempt. "+String(reason));
      setRetained(readAttempt(key));
    }finally{setPending(false);}
  };
  const inspect=async()=>{
    if(pending||!retained.raw)return;
    const raw=retained.raw;setPending(true);setFailure(null);setInspection(null);
    try{
      const attempt=parseAttempt(raw,project);
      const observed=(await bootstrap(project.id)).project;
      if(!observed||!sameProjectRevision(observed,project))throw new Error("Reopen the current saved project before reviewing this attempt.");
      if(observed.revision<=attempt.expected_revision)throw new Error("This attempt could still commit at its original revision. It will not be sent again.");
      if(observed.revision-attempt.expected_revision>2048)throw new Error("The history exceeds the bounded review window.");
      let cursor=attempt.expected_revision,matched=false;
      while(cursor<observed.revision){
        const page=await projectHistory(observed,cursor,256);
        if(!page.length||page.length>256)throw new Error("Saved history is incomplete.");
        for(const event of page){
          if(event.revision!==cursor+1||event.revision>observed.revision)throw new Error("Saved history is not contiguous.");
          if(event.revision===attempt.expected_revision+1&&event.change.type==="propagate_delivery_setting"&&sameValue(event.change.propagation,attempt))matched=true;
          cursor=event.revision;
        }
      }
      const after=(await bootstrap(project.id)).project;
      if(!after||!sameProjectRevision(after,observed)||!sameProjectRevision(currentProjectState().project,observed)
        ||window.localStorage.getItem(key)!==raw)throw new Error("The project or retained attempt changed during history review.");
      setInspection({raw,revision:observed.revision,matched});
    }catch(reason){setFailure(String(reason));}
    finally{setPending(false);}
  };
  const separate=()=>{
    if(!inspection||pending||currentProjectState().busy||!sameProjectRevision(currentProjectState().project,project)
      ||inspection.revision!==project.revision)return;
    try{
      removeExactAttempt(key,inspection.raw);
      setRetained(readAttempt(key));setInspection(null);setSelection([]);sent.current=false;setFailure(null);
    }catch(reason){setFailure(String(reason));}
  };
  return <section className="caption-export-panel" aria-label="Propagate saved delivery setting">
    <header><div><strong>Update related delivery profiles</strong>
      <span>Copy one saved setting into explicitly selected descendants.</span></div></header>
    {(retained.raw!==null||retained.error)&&<section aria-label="Retained delivery family attempt">
      <p role="status">{retained.error??"An exact previous update is retained. It will not be replayed automatically."}</p>
      {retained.raw!==null&&<><details><summary>Review original attempted values</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{retained.raw.slice(0,256000)}</pre></details>
        <button type="button" className="button" disabled={pending} onClick={()=>void inspect()}>Inspect saved family history</button>
        {inspection&&<><p>{inspection.matched?"The exact change is present at its original next revision.":"No exact matching change was found at its original next revision."} The saved project has advanced; the original attempt cannot newly commit at that earlier revision. This is a history comparison, not a native render or independent receipt.</p>
          <button type="button" className="button" disabled={pending||blocked} onClick={separate}>Release reviewed attempt and start separate selection</button></>}
      </>}
    </section>}
    {rows.length>0&&<>
      <p className="production-help">Source: {source.name}, revision r{project.revision}. Unselected profiles, cuts, dimensions, locale, text, voice, recipes and derivation history remain unchanged. Review each replacement; no automatic inheritance is inferred.</p>
      <label className="field-label">Shared setting
        <select aria-label="Shared delivery setting" value={field} disabled={disabled} onChange={e=>{
          setField(e.target.value as SharedDeliveryField);setSelection([]);setFailure(null);
        }}>{SHARED_DELIVERY_FIELDS.map(item=><option key={item.field} value={item.field}>{item.label}</option>)}</select>
      </label>
      <p>Saved source value: <strong>{displayDeliverySetting(value)}</strong></p>
      {field==="brand_profile"&&<p className="production-help">This updates the declared brand reference, not the visual content or a brand approval.</p>}
      <div className="localized-text-ledger">
        {rows.map(row=><label key={row.profile.id}>
          <input type="checkbox" style={{width:16,height:16,minHeight:16,margin:0}} aria-label={"Update "+row.profile.name} checked={selection.includes(row.profile.id)}
            disabled={disabled||!!row.reason} onChange={e=>setSelection(ids=>e.target.checked?[...ids,row.profile.id]:ids.filter(id=>id!==row.profile.id))}/>
          <span><strong>{row.profile.name}</strong><small>{displayDeliverySetting(row.expected)} to {displayDeliverySetting(value)}</small>
            {row.reason&&<small>{row.reason}</small>}</span>
        </label>)}
      </div>
      <button type="button" className="button" disabled={disabled||!selection.length} onClick={()=>void submit()}>
        {pending?"Saving selected profiles…":"Apply to "+selection.length+" selected profiles"}
      </button>
      <p className="production-help">One saved project revision. Native preflight and rendered media review remain separate.</p>
    </>}
    {failure&&<p className="portable-message error" role="alert">{failure}</p>}
  </section>;
}
