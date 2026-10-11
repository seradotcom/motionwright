import { useEffect, useState } from "react";
import { FileCheck2, RefreshCw, Save } from "lucide-react";
import type { Change, Claim, Project } from "./types";
import { validateCaptureRecord } from "./captureSource";
import type { CaptureSourceRecord } from "./captureSource";

type Props = {
  project: Project;
  busy: boolean;
  commit: (change: Change) => Promise<void>;
};
const sha=/^[0-9a-f]{64}$/;
const commitHash=/^[0-9a-f]{40}$/;
const sourceScope="first-party-synthetic-fixture-only";
const originalScenario="production-original-ui-local-v1";

function readable(error:unknown):string {
  return error instanceof Error?error.message:String(error);
}
function sourceRevision(claim:Claim|undefined):number|null {
  return claim?.source?.kind==="project_revision"?claim.source.revision:null;
}

/** First-party, claim-linked evidence admission only. Never captures the desktop,
 * reads OS credentials or interprets an imported PNG as verified product behavior.
 */
export default function CaptureSourceWorkbench({project,busy,commit}:Props){
  const registered=project.production_design?.captures??[];
  const images=project.assets.filter(a=>a.media_type==="image/png" && sha.test(a.content_sha256??""));
  const manifests=project.assets.filter(a=>a.media_type==="application/json" && sha.test(a.content_sha256??""));
  const claims=project.brief.claims.filter(c=>{
    const revision=sourceRevision(c);
    return c.context==="synthetic-first-party-capture-validation"
      && revision!==null && revision<=project.revision;
  });
  const [base,setBase]=useState(project.revision);
  const [claimText,setClaimText]=useState("");
  const [claimId,setClaimId]=useState("");
  const [screenshotId,setScreenshotId]=useState("");
  const [manifestId,setManifestId]=useState("");
  const [fixtureSha,setFixtureSha]=useState("");
  const [sourceCommit,setSourceCommit]=useState("");
  const [version,setVersion]=useState("0.1.0");
  const [maskX,setMaskX]=useState("");
  const [maskY,setMaskY]=useState("");
  const [maskWidth,setMaskWidth]=useState("");
  const [maskHeight,setMaskHeight]=useState("");
  const [error,setError]=useState<string|null>(null);
  const stale=base!==project.revision;
  const locked=project.locks.some(lock=>
    lock.resource==="project:"+project.id && lock.kind==="content");
  useEffect(()=>{
    setBase(project.revision);
    setClaimId("");setScreenshotId("");setManifestId("");
    setFixtureSha("");setSourceCommit("");setMaskX("");setMaskY("");
    setMaskWidth("");setMaskHeight("");setError(null);
    // The draft is intentionally preserved across same-generation revisions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[project.generation]);

  const screenshot=images.find(a=>a.id===screenshotId);
  const manifest=manifests.find(a=>a.id===manifestId);
  const claim=claims.find(c=>c.id===claimId);
  const revision=sourceRevision(claim);
  function candidate():CaptureSourceRecord {
    if(!screenshot?.content_sha256 || !manifest?.content_sha256
      || revision===null || !sha.test(fixtureSha) || !commitHash.test(sourceCommit)
      || [maskX,maskY,maskWidth,maskHeight].some(value=>value.trim()==="")){
      throw new Error("Choose a source PNG, JSON receipt, pinned claim and SHA-256, plus explicit redaction coordinates.");
    }
    const record:CaptureSourceRecord={
      schema:"motionwright.capture-source/1",
      id:crypto.randomUUID(),screenshot_asset_id:screenshot.id,
      screenshot_sha256:screenshot.content_sha256,
      manifest_asset_id:manifest.id,manifest_sha256:manifest.content_sha256,
      fixture_state_sha256:fixtureSha,claim_id:claim!.id,
      source_project_id:project.id,source_generation:project.generation,
      source_revision:revision,
      app_id:"motionwright-studio",app_version:version,source_commit:sourceCommit,
      scenario_id:originalScenario,origin:"http://127.0.0.1:1420",route:"/",
      viewport_width:1440,viewport_height:940,device_scale_factor:1,
      screenshot_repeat_sha256:screenshot.content_sha256,
      redaction:{
        selector:".revision-chip",x:Number(maskX),y:Number(maskY),
        width:Number(maskWidth),height:Number(maskHeight),mask_color:"#0B0B0B",
      },
      external_requests:0,credential_fields:0,declared_scope:sourceScope,
    };
    validateCaptureRecord(project,record);
    return record;
  }
  const preflight=(()=>{
    try{candidate();return null;}catch(e){return readable(e);}
  })();
  const canRegister=!stale && !busy && !locked && !preflight;
  async function register(){
    if(!canRegister)return;
    setError(null);
    try{
      await commit({type:"register_capture_source",record:candidate()});
      setBase(project.revision+1);
    }catch(e){setError(readable(e));}
  }
  async function addClaim(){
    if(stale||busy||locked||claimText.trim().length<10)return;
    setError(null);
    try{
      await commit({type:"add_claim",text:claimText.trim(),
        source:{kind:"project_revision",revision:project.revision},
        context:"synthetic-first-party-capture-validation",
        source_revision:sourceCommit||"first-party-declared"});
      setBase(project.revision+1);
      setClaimText("");
    }catch(e){setError(readable(e));}
  }
  return <section className="production-plan" aria-label="First-party capture provenance">
    <div className="production-section-title">
      <FileCheck2 size={18}/>
      <div><h2>First-party capture provenance</h2>
        <p>Immutable imported PNG/JSON identities · synthetic fixtures only</p></div>
    </div>
    <p className="production-help">
      This interface records the evidence of a previously performed, reviewed first-party
      browser capture. It cannot take screenshots, import customer sessions, verify content
      truth or substitute media into a deliverable. Import the already-redacted PNG and
      JSON receipt through the project's existing Assets workflow before registering.
      Verify source bytes and mask pixels using the independent capture evidence test.
    </p>
    {stale && <div className="production-notice" role="status">
      <strong>Draft from revision {base}; project is now revision {project.revision}.</strong>
      <span>Source metadata was not overwritten. Reload to reconcile it.</span>
      <button className="secondary-button" onClick={()=>{setBase(project.revision);setError(null);}}>
        <RefreshCw size={14}/> Reload source revision</button>
    </div>}
    {!images.length && <p role="status">No imported SHA-256 PNG source asset is available.</p>}
    {!manifests.length && <p role="status">No imported SHA-256 JSON capture receipt is available.</p>}
    {!claims.length && <p role="status">No authored synthetic first-party source claim is available.</p>}
    <div className="production-section-title"><h3>1. Author a source-revision claim</h3></div>
    <label className="production-field"><span>Precisely describe an observable first-party screen fact</span>
      <textarea aria-label="First-party capture claim" rows={2} value={claimText}
        placeholder="Describe only what the source screenshot actually demonstrates"
        onChange={e=>setClaimText(e.target.value)}/>
    </label>
    <button className="secondary-button" disabled={busy||stale||locked||claimText.trim().length<10}
      onClick={()=>void addClaim()}>
      <Save size={14}/> Record source-revision claim
    </button>
    <div className="production-section-title"><h3>2. Register reviewed imported evidence</h3></div>
    <div className="production-plan-grid">
      <label className="production-field"><span>First-party source claim</span>
        <select aria-label="Capture source claim" value={claimId} onChange={e=>setClaimId(e.target.value)}>
          <option value="">Choose an existing source-revision claim</option>
          {claims.map(c=><option key={c.id} value={c.id}>
            {c.text.slice(0,64)} · revision {sourceRevision(c)}</option>)}
        </select>
      </label>
      <label className="production-field"><span>Redacted PNG from Assets</span>
        <select aria-label="Capture PNG asset" value={screenshotId} onChange={e=>setScreenshotId(e.target.value)}>
          <option value="">Choose immutable PNG source</option>
          {images.map(a=><option key={a.id} value={a.id}>{a.name} · {a.content_sha256?.slice(0,12)}…</option>)}
        </select>
      </label>
      <label className="production-field"><span>JSON receipt from Assets</span>
        <select aria-label="Capture JSON asset" value={manifestId} onChange={e=>setManifestId(e.target.value)}>
          <option value="">Choose immutable JSON receipt</option>
          {manifests.map(a=><option key={a.id} value={a.id}>{a.name} · {a.content_sha256?.slice(0,12)}…</option>)}
        </select>
      </label>
      <label className="production-field"><span>Original fixture state SHA-256</span>
        <input aria-label="Capture fixture SHA-256" value={fixtureSha} maxLength={64}
          spellCheck={false} onChange={e=>setFixtureSha(e.target.value)}
          placeholder="Exact 64-digit source state hash"/>
      </label>
      <label className="production-field"><span>Studio source commit</span>
        <input aria-label="Capture source commit" maxLength={40} spellCheck={false}
          value={sourceCommit} onChange={e=>setSourceCommit(e.target.value)}
          placeholder="Exact 40-digit Git commit"/>
      </label>
      <label className="production-field"><span>Captured Studio version</span>
        <input aria-label="Capture Studio version" value={version}
          onChange={e=>setVersion(e.target.value)}/>
      </label>
      {([
        ["maskX","Redaction X",maskX,setMaskX],
        ["maskY","Redaction Y",maskY,setMaskY],
        ["maskWidth","Redaction width",maskWidth,setMaskWidth],
        ["maskHeight","Redaction height",maskHeight,setMaskHeight],
      ] as const).map(([key,label,value,update])=><label key={key} className="production-field">
        <span>{label} (px)</span>
        <input aria-label={label} inputMode="numeric" value={value}
          onChange={e=>update(e.target.value)}/>
      </label>)}
    </div>
    <p className="production-help">
      Fixed authorization: Motionwright Studio / local origin / Chromium 1440 × 940 /
      no credentials or external requests. The only admitted mask selector is
      .revision-chip, opaque #0B0B0B. Values must come from the actual independent
      capture manifest. No screenshot is created or inspected by this form.
    </p>
    {preflight && <p role="status" className="production-help">Admission preflight: {preflight}</p>}
    {error && <p role="alert" className="production-error">{error}</p>}
    <button className="primary-button" disabled={!canRegister} onClick={()=>void register()}>
      <Save size={14}/> Register first-party capture source
    </button>
    <div className="production-section-title"><h3>Bound provenance records</h3></div>
    <div className="production-capsule-list">
      {registered.map(c=><article key={c.id}>
        <header><strong>Original Studio capture · {c.source_revision}</strong>
          <span>Source declaration / human review required</span></header>
        <p className="mono">PNG sha256:{c.screenshot_sha256}</p>
        <p className="mono">Manifest sha256:{c.manifest_sha256}</p>
        <p className="mono">Fixture sha256:{c.fixture_state_sha256}</p>
        <p>App revision {c.source_commit.slice(0,12)}… · {c.app_version} ·
          {c.viewport_width} × {c.viewport_height} · {c.declared_scope}</p>
        <p>Redacted source metadata, not independent proof of customer usage,
          privacy clearance or creative approval.</p>
      </article>)}
      {!registered.length && <p className="production-help">No first-party evidence sources registered in this project.</p>}
    </div>
  </section>;
}
