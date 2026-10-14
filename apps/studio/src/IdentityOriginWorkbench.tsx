import { useEffect, useState } from "react";
import { Ban, RefreshCw, Save, ShieldCheck } from "lucide-react";
import type { Change, Project } from "./types";
import {
  DISCLOSURE_FICTIONAL, DISCLOSURE_HUMAN, IDENTITY_SOURCE_SCHEMA,
  identityUseReview, validateIdentityOrigin,
} from "./identityOrigin";
import type { IdentityModality, IdentityOrigin, IdentityOriginRecord, IdentityPurpose } from "./identityOrigin";

const MODES:IdentityModality[]=["voice","face","presenter"];
const USES:IdentityPurpose[]=["product_demonstration","marketing","education","internal_review"];
const iso=(ms:number)=>new Date(ms).toISOString().replace(".000Z","Z");
const explain=(error:unknown)=>error instanceof Error?error.message:String(error);
type Props={project:Project;commit:(change:Change)=>Promise<void>;busy:boolean};

export default function IdentityOriginWorkbench({project,commit,busy}:Props){
  const sources=project.production_design?.identity_origins??[];
  const revoked=project.production_design?.identity_revocations??[];
  const assets=project.assets.filter(a=>a.media_type==="application/json" && /^[0-9a-f]{64}$/.test(a.content_sha256??""));
  const [base,setBase]=useState(project.revision);
  const [kind,setKind]=useState<IdentityOrigin>("original_fictional");
  const [label,setLabel]=useState("");
  const [description,setDescription]=useState("");
  const [declaration,setDeclaration]=useState("");
  const [sourceId,setSourceId]=useState("");
  const [consentId,setConsentId]=useState("");
  const [subject,setSubject]=useState("");
  const [scope,setScope]=useState("");
  const [modalities,setModalities]=useState<IdentityModality[]>([]);
  const [purposes,setPurposes]=useState<IdentityPurpose[]>([]);
  const [issued,setIssued]=useState(()=>iso(Date.now()));
  const [expiry,setExpiry]=useState(()=>iso(Date.now()+30*86_400_000));
  const [reason,setReason]=useState("");
  const [error,setError]=useState<string|null>(null);
  const [lastAction,setLastAction]=useState<string|null>(null);
  const stale=base!==project.revision;
  const locked=project.locks.some(lock=>lock.resource==="project:"+project.id&&lock.kind==="content");

  useEffect(()=>{
    setBase(project.revision);setError(null);setLastAction(null);
    // Preserve human drafts when another edit increments this generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation]);

  function draft():IdentityOriginRecord {
    const imported=assets.find(a=>a.id===sourceId);
    const consent=assets.find(a=>a.id===consentId);
    const record:IdentityOriginRecord={
      schema:IDENTITY_SOURCE_SCHEMA,
      id:crypto.randomUUID(),source_project_id:project.id,
      source_generation:project.generation,origin:kind,
      display_label:label,origin_description:description,
      created_by_declaration:declaration,
      created_at_utc:issued,expires_at_utc:expiry,
      allowed_modalities:modalities,allowed_purposes:purposes,
      provenance_asset_id:sourceId,provenance_sha256:imported?.content_sha256??"",
      consent_asset_id:kind==="human_likeness"?consentId:null,
      consent_sha256:kind==="human_likeness"?consent?.content_sha256??"":null,
      consent_subject_declaration:kind==="human_likeness"?subject:null,
      consent_scope_declaration:kind==="human_likeness"?scope:null,
      synthetic_disclosure:kind==="human_likeness"?DISCLOSURE_HUMAN:DISCLOSURE_FICTIONAL,
    };
    validateIdentityOrigin(project,record);
    return record;
  }
  const preflight=(()=>{
    try{draft();return null;}catch(e){return explain(e);}
  })();
  async function register(){
    if(stale||busy||locked||preflight)return;
    setError(null);setLastAction(null);
    try{
      await commit({type:"register_identity_origin",record:draft()});
      setBase(project.revision+1);setLastAction("Identity origin registered as a declaration; no media-generation permission was granted.");
    }catch(e){setError(explain(e));}
  }
  async function revoke(id:string){
    if(stale||busy||locked||!reason.trim())return;
    setError(null);
    try{
      await commit({type:"revoke_identity_origin",revocation:{
        id:crypto.randomUUID(),identity_id:id,revoked_at_utc:iso(Date.now()),reason:reason.trim(),
      }});
      setBase(project.revision+1);setReason("");
      setLastAction("Revocation recorded. Previously registered provenance remains immutable.");
    }catch(e){setError(explain(e));}
  }
  function toggle<T extends string>(items:T[],value:T,update:(next:T[])=>void){
    update(items.includes(value)?items.filter(a=>a!==value):[...items,value]);
  }
  return <div className="production-split">
    <section className="production-inspector" aria-label="Identity source controls">
      <div className="production-section-title"><ShieldCheck size={18}/>
        <div><h2>Identity and consent</h2><p>Rights-declared original sources · never an execution grant</p></div>
      </div>
      <p className="production-help">
        Register the declared origin and narrowly scoped purposes of a synthetic
        voice, face or presenter. A referenced document is not proof of ownership,
        identity or an authenticated signature. Human likenesses always require
        independent consent review. No voice, portrait or presenter is generated here.
      </p>
      {stale && <div className="production-notice" role="status">
        <strong>Origin draft based on revision {base}; project is now {project.revision}.</strong>
        <button className="secondary-button" onClick={()=>{setBase(project.revision);setError(null);}}>
          <RefreshCw size={14}/> Reconcile current revision
        </button>
      </div>}
      <label className="production-field"><span>Identity category</span>
        <select aria-label="Identity origin category" value={kind}
          onChange={e=>{setKind(e.target.value as IdentityOrigin);setError(null);}}>
          <option value="original_fictional">Entirely fictional original character</option>
          <option value="human_likeness">Real human likeness · separate consent evidence required</option>
        </select>
      </label>
      <label className="production-field"><span>Project-local name</span>
        <input aria-label="Identity display label" value={label} maxLength={96}
          onChange={e=>setLabel(e.target.value)}/></label>
      <label className="production-field"><span>Where the source comes from</span>
        <textarea aria-label="Identity origin description" rows={2} maxLength={300}
          value={description} onChange={e=>setDescription(e.target.value)}/></label>
      <label className="production-field"><span>Author's rights declaration</span>
        <textarea aria-label="Identity owner declaration" rows={2} maxLength={160}
          value={declaration} onChange={e=>setDeclaration(e.target.value)}/></label>
      <label className="production-field"><span>Original provenance JSON Asset</span>
        <select aria-label="Identity provenance source" value={sourceId}
          onChange={e=>setSourceId(e.target.value)}>
          <option value="">Select an independently imported JSON source</option>
          {assets.map(a=><option key={a.id} value={a.id}>{a.name} · {a.content_sha256?.slice(0,12)}…</option>)}
        </select>
      </label>
      {kind==="human_likeness" && <>
        <label className="production-field"><span>Distinct consent JSON Asset</span>
          <select aria-label="Identity consent evidence" value={consentId}
            onChange={e=>setConsentId(e.target.value)}>
            <option value="">Select an independently imported consent source</option>
            {assets.filter(a=>a.id!==sourceId).map(a=>
              <option key={a.id} value={a.id}>{a.name} · {a.content_sha256?.slice(0,12)}…</option>)}
          </select>
        </label>
        <label className="production-field"><span>Person's name as declared in consent</span>
          <input aria-label="Identity consent subject" maxLength={160} value={subject}
            onChange={e=>setSubject(e.target.value)}/></label>
        <label className="production-field"><span>Exact permitted use as declared</span>
          <textarea aria-label="Identity consent scope" maxLength={300} rows={2}
            value={scope} onChange={e=>setScope(e.target.value)}/></label>
        <p className="production-error">Consent bytes and an assertion do not authorize likeness synthesis without independent approval.</p>
      </>}
      <div className="production-section-title"><h3>Permitted media and purposes</h3></div>
      {MODES.map(value=><label className="production-check" key={value}>
        <input type="checkbox" aria-label={"Identity modality "+value}
          checked={modalities.includes(value)}
          onChange={()=>toggle(modalities,value,setModalities)}/> {value}
      </label>)}
      {USES.map(value=><label className="production-check" key={value}>
        <input type="checkbox" aria-label={"Identity purpose "+value}
          checked={purposes.includes(value)}
          onChange={()=>toggle(purposes,value,setPurposes)}/> {value.replaceAll("_"," ")}
      </label>)}
      <div className="production-plan-grid">
        <label className="production-field"><span>Origin declaration UTC</span>
          <input aria-label="Identity issue UTC" value={issued}
            onChange={e=>setIssued(e.target.value)}/></label>
        <label className="production-field"><span>Expiry UTC (max 366 days)</span>
          <input aria-label="Identity expiry UTC" value={expiry}
            onChange={e=>setExpiry(e.target.value)}/></label>
      </div>
      <p className="production-help">{kind==="human_likeness"?DISCLOSURE_HUMAN:DISCLOSURE_FICTIONAL}.
        Disclosure is mandatory for subsequent content; this record does not certify
        that a renderer has displayed it or that use has been legally reviewed.</p>
      {preflight && <p role="status" className="production-help">Admission gate: {preflight}</p>}
      {error && <p role="alert" className="production-error">{error}</p>}
      {lastAction && <p role="status">{lastAction}</p>}
      <button className="primary-button" onClick={()=>void register()}
        disabled={!!preflight||stale||busy||locked}>
        <Save size={14}/> Register declared identity source
      </button>
    </section>
    <section className="production-viewer" aria-label="Identity source receipts">
      <div className="production-section-title"><div>
        <h2>Registered identity sources</h2>
        <p>Append-only provenance and revocation history</p></div></div>
      <label className="production-field"><span>Reason to revoke a declared identity</span>
        <textarea aria-label="Identity revocation reason" maxLength={240} rows={2}
          value={reason} onChange={e=>setReason(e.target.value)}/></label>
      <div className="production-capsule-list">
        {sources.map(item=>{
          const revokedItem=revoked.find(event=>event.identity_id===item.id);
          const status=identityUseReview(project,item.id,iso(Date.now()),
            item.allowed_modalities[0],item.allowed_purposes[0]);
          return <article key={item.id}>
            <header><strong>{item.display_label}</strong>
              <span>{item.origin==="human_likeness"?"Human likeness":"Fictional original"} · {status.replaceAll("_"," ")}</span>
            </header>
            <p>Declared purposes: {item.allowed_purposes.join(", ")} · media: {item.allowed_modalities.join(", ")}</p>
            <p>Expiry: {item.expires_at_utc}</p>
            <p className="mono">Provenance SHA-256: {item.provenance_sha256}</p>
            <p>{item.synthetic_disclosure}</p>
            {revokedItem?<p>Revoked {revokedItem.revoked_at_utc} · {revokedItem.reason}</p>:
              <button className="secondary-button" disabled={stale||busy||locked||!reason.trim()}
                onClick={()=>void revoke(item.id)}>
                <Ban size={14}/> Revoke this source
              </button>}
          </article>;
        })}
        {!sources.length&&<p className="production-help">
          No identity sources are registered. Imported rights declarations are
          never converted into a presenter or voice automatically.
        </p>}
      </div>
    </section>
  </div>;
}
