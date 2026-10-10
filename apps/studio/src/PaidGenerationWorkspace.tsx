import {useEffect,useState} from 'react';
import {FileCheck,RefreshCw,ShieldCheck,ShieldAlert} from 'lucide-react';
import type {Change,Project,Scene} from './types';
import type {
 PaidGenerationKind,PaidGenerationSpec,PaidGenerationEdit,
 PaidGenerationDraftPreview,PaidGenerationReservationPreview,
 PaidGenerationJournalObservation
} from './paidGenerationTypes';
import {
 nativePaidGenerationJournal,nativePaidGenerationPreflight,
 nativePaidGenerationReservePreflight
} from './api';

const hex=/^[0-9a-f]{64}$/;
const rootless=():boolean=>typeof window!=='undefined'&&'__TAURI_INTERNALS__'in window;
const displayMoney=(micros:number)=>{
 if(!Number.isSafeInteger(micros)||micros<0)return 'INVALID COST';
 const whole=Math.floor(micros/1_000_000);
 const fractional=String(micros%1_000_000).padStart(6,'0');
 return `$${whole}.${fractional} USD (declared, unverified)`;
};
function toMicroUsd(value:string):number{
 const found=/^(0|[1-9][0-9]{0,5})(?:\.([0-9]{1,6}))?$/.exec(value.trim());
 if(!found)throw new Error('Budget must be an exact nonnegative USD amount with at most six fractional digits.');
 const amount=BigInt(found[1])*1_000_000n+BigInt((found[2]??'').padEnd(6,'0')||'0');
 if(amount<1n||amount>100_000_000_000n)throw new Error('One generation attempt must reserve $0.000001–$100,000.');
 return Number(amount);
}
async function sourceDigest(value:string):Promise<string>{
 if(!value.trim()||value.length>12000)throw new Error('Enter a bounded source prompt; it will not be sent to a provider.');
 const bytes=new TextEncoder().encode(value);
 return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))]
   .map(n=>n.toString(16).padStart(2,'0')).join('');
}
const errorText=(e:unknown)=>e instanceof Error?e.message:String(e);
type Candidate={kind:'create'|'reserve';edit:PaidGenerationEdit;baseRevision:number;
 message:string;preview:PaidGenerationDraftPreview|PaidGenerationReservationPreview};
export default function PaidGenerationWorkspace({
 project,scene,busy,commit
}:{project:Project;scene:Scene|null;busy:boolean;commit:(change:Change)=>Promise<void>}){
 const [journal,setJournal]=useState<PaidGenerationJournalObservation|null>(null);
 const [loading,setLoading]=useState(false);
 const [working,setWorking]=useState(false);
 const [error,setError]=useState<string|null>(null);
 const [pending,setPending]=useState<Candidate|null>(null);
 const [draftId,setDraftId]=useState(()=>crypto.randomUUID());
 const [campaignId,setCampaignId]=useState(()=>crypto.randomUUID());
 const [kind,setKind]=useState<PaidGenerationKind>('image');
 const [provider,setProvider]=useState('original_provider');
 const [model,setModel]=useState('original_model');
 const [version,setVersion]=useState('v1');
 const [prompt,setPrompt]=useState('An original synthetic visual study with no real people or fabricated product claims.');
 const [rights,setRights]=useState('Original synthetic study; no publication or identity cloning.');
 const [visibleLabel,setVisibleLabel]=useState('SYNTHETIC ORIGINAL / NOT REAL PRODUCT EVIDENCE');
 const [capabilitySha,setCapabilitySha]=useState('');
 const [policySha,setPolicySha]=useState('');
 const [budgetUsd,setBudgetUsd]=useState('1.00');
 const native=rootless();
 useEffect(()=>{setPending(null);setError(null);},[project.id,project.generation,project.revision]);
 useEffect(()=>{
  let active=true;
  setJournal(null);
  if(!native)return;
  setLoading(true);
  nativePaidGenerationJournal(project).then(value=>{
   if(active)setJournal(value);
  }).catch(err=>{if(active)setError(errorText(err));}).finally(()=>{
   if(active)setLoading(false);
  });
  return()=>{active=false;};
 },[native,project.id,project.generation,project.revision]);
 const preview=async(action:'create'|'reserve',jobId?:string)=>{
  setWorking(true);setError(null);setPending(null);
  try{
   if(!native)throw new Error('The browser demo cannot register paid jobs or real provider balances.');
   if(action==='reserve'){
    if(!jobId)throw new Error('Select a persistent draft to preview its reservation.');
    const checked=await nativePaidGenerationReservePreflight(project,jobId);
    if(checked.provider_called||checked.payment_charged||checked.owner_execution_grant)
     throw new Error('Unsafe paid reservation preflight response.');
    setPending({kind:'reserve',edit:checked.normalized_edit,preview:checked,
      baseRevision:project.revision,
      message:'Record the fixed maximum budget once in SQLite. This does not call or charge a provider.'});
    return;
   }
   if(!scene)throw new Error('Select an existing saved scene before preparing a paid source request.');
   if(!hex.test(capabilitySha)||!hex.test(policySha))
    throw new Error('Provide the exact SHA-256 of the owner-reviewed provider capability and usage-policy records. They are metadata declarations, not verified API credentials.');
   const maxCharge=toMicroUsd(budgetUsd);
   const spec:PaidGenerationSpec={
    id:draftId,campaign_id:campaignId,scene_id:scene.id,variant_index:0,
    kind,scope:{kind:'pilot'},provider_id:provider,model_id:model,
    model_version:version,capability_receipt_sha256:capabilitySha,
    rights_policy_sha256:policySha,prompt_sha256:await sourceDigest(prompt),
    inputs:[],output_usage_terms:rights,
    identity:{kind:'synthetic_original',visible_synthetic_label:visibleLabel},
    max_charge_microusd:maxCharge,logical_idempotency_sha256:''
   };
   const checked=await nativePaidGenerationPreflight(project,spec);
   if(checked.provider_called||checked.payment_charged||checked.owner_execution_grant
     ||checked.committed||checked.provider_capability_signature_verified)
     throw new Error('Preflight tried to imply remote execution or verified provider identity.');
   setPending({kind:'create',edit:checked.normalized_edit,preview:checked,
    baseRevision:project.revision,
    message:'Save this pilot INTENT only. No media generation, API request, payment or consent is authorized.'});
  }catch(e){setError(errorText(e));}finally{setWorking(false);}
 };
 const save=async()=>{
  if(!pending)return;
  setWorking(true);setError(null);
  try{
   if(project.revision!==pending.baseRevision)
    throw new Error('Project revision changed since the paid-job preflight. Review the latest journal.');
   await commit({type:'edit_paid_generation',edit:pending.edit});
   setPending(null);
  }catch(e){setError(errorText(e));}finally{setWorking(false);}
 };
 return <section className="production-plan" aria-label="Paid generation source intent and budget journal">
   <div className="production-section-title"><ShieldCheck size={18}/><div>
    <h2>Paid-generation source journal</h2>
    <p>Owner decisions and uncertain external tasks. No live provider connection, charge, signed consent or generated output.</p>
   </div></div>
   <p className="production-help">This version records exact semantic intent, one budget reservation, source/consent metadata and an auditable task outcome. It does not promise a provider job is available. Real image, video, TTS or presenter generation requires a separately installed, owner-authorized provider adapter.</p>
   {!native&&<p role="status" className="production-notice">Browser demo — read-only; cannot fabricate paid API calls, charges, tasks or approvals.</p>}
   <div className="production-plan-grid">
    <div className="native-difference"><strong>Maximum budgets reserved</strong>
     <p>{journal?displayMoney(journal.budget.project_reserved_microusd):'Unverified / unavailable'}</p>
    </div>
    <div className="native-difference"><strong>Reserved against uncertain outcomes</strong>
     <p>{journal?displayMoney(journal.budget.unresolved_reserved_microusd):'Unverified / unavailable'}</p>
    </div>
   </div>
   <div className="production-section-title"><FileCheck size={17}/><div>
    <h3>Prepare an original synthetic pilot</h3>
    <p>A pilot must be accepted before any later batch can reserve spend.</p>
   </div></div>
   <div className="production-plan-grid">
    <label className="production-field"><span>Original capability</span><select value={kind} disabled={working||busy}
      onChange={e=>{setKind(e.target.value as PaidGenerationKind);setPending(null);}}>
      <option value="image">Image</option><option value="video">Video</option><option value="tts">Original TTS voice</option><option value="presenter">Original synthetic presenter</option>
    </select></label>
    <label className="production-field"><span>Maximum recorded attempt USD</span>
      <input value={budgetUsd} disabled={working||busy} onChange={e=>{setBudgetUsd(e.target.value);setPending(null);}}/>
    </label>
    <label className="production-field"><span>Declared provider ID</span><input value={provider} maxLength={128}
      onChange={e=>{setProvider(e.target.value);setPending(null);}} disabled={working||busy}/></label>
    <label className="production-field"><span>Declared model ID</span><input value={model} maxLength={128}
      onChange={e=>{setModel(e.target.value);setPending(null);}} disabled={working||busy}/></label>
    <label className="production-field"><span>Model version</span><input value={version} maxLength={128}
      onChange={e=>{setVersion(e.target.value);setPending(null);}} disabled={working||busy}/></label>
    <label className="production-field"><span>Provider capability source SHA-256</span>
      <input value={capabilitySha} maxLength={64} spellCheck={false} placeholder="64 lowercase hex"
      onChange={e=>{setCapabilitySha(e.target.value.trim().toLowerCase());setPending(null);}} disabled={working||busy}/></label>
    <label className="production-field"><span>Usage policy source SHA-256</span>
      <input value={policySha} maxLength={64} spellCheck={false} placeholder="64 lowercase hex"
      onChange={e=>{setPolicySha(e.target.value.trim().toLowerCase());setPending(null);}} disabled={working||busy}/></label>
   </div>
   <label className="production-field"><span>Original creative prompt (local hash only)</span><textarea rows={3} value={prompt} maxLength={12000}
     disabled={working||busy} onChange={e=>{setPrompt(e.target.value);setPending(null);}}/></label>
   <label className="production-field"><span>Declared output rights/purpose</span><textarea rows={2} value={rights} maxLength={512}
     disabled={working||busy} onChange={e=>{setRights(e.target.value);setPending(null);}}/></label>
   <label className="production-field"><span>Visible synthetic disclosure for the resulting media</span><input value={visibleLabel} maxLength={300}
     disabled={working||busy} onChange={e=>{setVisibleLabel(e.target.value);setPending(null);}}/></label>
   <div className="production-button-row">
    <button type="button" className="secondary-button" disabled={!native||!scene||busy||working}
      onClick={()=>void preview('create')}><ShieldCheck size={14}/> Preflight original pilot (no spend)</button>
    <button type="button" className="secondary-button" disabled={busy||working}
      onClick={()=>{setDraftId(crypto.randomUUID());setCampaignId(crypto.randomUUID());setPending(null);}}>New campaign identity</button>
   </div>
   {error&&<p className="production-error" role="alert">{error}</p>}
   {pending&&<div className="native-difference" role="status">
    <strong>{pending.kind==='create'?'Original pilot intent preview':'Recorded budget reservation preview'}</strong>
    <p>{pending.message}</p>
    <p>Based on exact project revision {pending.baseRevision}; provider signature, consent authentication, task execution and billing remain unverified.</p>
    <button type="button" className="primary-button" disabled={busy||working||pending.baseRevision!==project.revision}
      onClick={()=>void save()}>Commit this project ledger record only</button>
   </div>}
   <div className="production-section-title"><RefreshCw size={17}/><div>
    <h3>Persistent logical requests</h3><p>Unknown provider outcomes must be reconciled with the same task ID or idempotency key; never re-submitted automatically.</p>
   </div></div>
   {loading&&<p className="production-help">Reading revision-bound SQLite journal…</p>}
   {journal?.rows.length===0&&<p className="production-help">No saved paid-generation intent exists in this project yet.</p>}
   {journal?.rows.map(row=><article key={row.job_id} className="native-difference">
    <strong>{row.kind} · {row.state.replaceAll('_',' ')} · {row.model_id} / {row.model_version}</strong>
    <p className="mono">Intent {row.source_sha256.slice(0,18)}… · task {row.known_task_id??'not confirmed'}</p>
    <p>Per-attempt cap: {displayMoney(row.max_charge_microusd)} · report: {row.reported_spend_microusd===null?'unknown':displayMoney(row.reported_spend_microusd)}</p>
    {row.reconciliation_query&&<p className="production-help"><ShieldAlert size={13}/> UNKNOWN outcome — query only existing task {row.reconciliation_query.known_task_id??'by semantic idempotency key'}. No new paid submission.</p>}
    {row.state==='draft'&&<button type="button" className="secondary-button" disabled={!native||busy||working}
      onClick={()=>void preview('reserve',row.job_id)}>Preflight ledger budget reservation (NOT a payment)</button>}
   </article>)}
 </section>;
}
