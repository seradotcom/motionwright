import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Flag, RefreshCw } from "lucide-react";
import { readNativePreviewFrame } from "./api";
import { planNativeInspection } from "./nativeInspection";
import { nativeFindingSource, nativeFindingUnknown, nativeFindingCurrent } from "./nativeFindingDraft";
import { useNativeFindingDraft } from "./useNativeFindingDraft";
import { createNativeReviewRepairDraft } from "./nativeReviewRepair";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import type { NativeSampleEvidence } from "./nativeInspection";
import type { Change, DeliverableProfile, MotionCanvasRenderEvidence, Project, Scene } from "./types";
import { seconds } from "./types";

interface Sample extends NativeSampleEvidence { url:string; }

async function thumbnail(bytes:Uint8Array,width:number,height:number):Promise<string> {
  if(bytes.length<24 || bytes.length>16*1024*1024) throw new Error("Native PNG exceeds the inspection byte budget.");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  if(view.getUint32(16)!==width || view.getUint32(20)!==height || width*height>16_777_216) throw new Error("Native PNG dimensions differ from the verified profile or exceed the decode budget.");
  if(typeof createImageBitmap!=="function") throw new Error("This WebView does not support bounded thumbnail decoding. Use the native frame stage instead.");
  const copy=new Uint8Array(bytes.length);copy.set(bytes);
  const targetWidth=Math.min(width,384),targetHeight=Math.max(1,Math.round(height*targetWidth/width));
  const bitmap=await createImageBitmap(new Blob([copy.buffer],{type:"image/png"}),{resizeWidth:targetWidth,resizeHeight:targetHeight,resizeQuality:"high"});
  try {
    const canvas=document.createElement("canvas");canvas.width=targetWidth;canvas.height=targetHeight;
    const context=canvas.getContext("2d");if(!context) throw new Error("Bounded image preview is unavailable.");
    context.drawImage(bitmap,0,0,targetWidth,targetHeight);
    const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error("Could not encode the native thumbnail.")),"image/png"));
    return URL.createObjectURL(blob);
  } finally {bitmap.close();}
}
function NativeComparison({first,second,mode}:{first:Sample;second:Sample;mode:"onion"|"difference"}) {
  const ref=useRef<HTMLCanvasElement>(null);const [error,setError]=useState<string|null>(null);
  useEffect(()=>{
    let active=true;setError(null);
    const load=async(url:string)=>{const image=new Image();image.src=url;await image.decode();return image;};
    Promise.all([load(first.url),load(second.url)]).then(([a,b])=>{
      if(!active || !ref.current) return;
      const canvas=ref.current;canvas.width=a.naturalWidth;canvas.height=a.naturalHeight;
      const context=canvas.getContext("2d");if(!context) throw new Error("Comparison canvas unavailable.");
      context.clearRect(0,0,canvas.width,canvas.height);context.globalAlpha=1;context.globalCompositeOperation="source-over";
      context.drawImage(a,0,0,canvas.width,canvas.height);
      if(mode==="onion") context.globalAlpha=.5; else context.globalCompositeOperation="difference";
      context.drawImage(b,0,0,canvas.width,canvas.height);context.globalAlpha=1;context.globalCompositeOperation="source-over";
    }).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:String(reason));});
    return()=>{active=false;};
  },[first.url,second.url,mode]);
  return <div className="native-inspection-comparison">{error?<p role="alert">{error}</p>:<canvas ref={ref} aria-label={mode==="onion"?"Onion overlay of two native-frame thumbnails":"Absolute channel difference of two native-frame thumbnails"}/>}</div>;
}
export default function NativeInspectionWorkbench({project,scene,displayProfile,evidence,commit,busy,onSeek,onOpenRender,onDraftRepair}: {
  project:Project;scene:Scene|null;displayProfile:DeliverableProfile|null;evidence:MotionCanvasRenderEvidence|null;
  commit:(change:Change)=>Promise<Project>;busy:boolean;onSeek:(time:number)=>void;onOpenRender:()=>void;
  onDraftRepair:(draft:NativeReviewRepairDraft)=>void;
}) {
  const plan=useMemo(()=>planNativeInspection(project,scene,displayProfile,evidence),[project,scene,displayProfile,evidence]);
  const [samples,setSamples]=useState<Sample[]>([]);const [loading,setLoading]=useState(false);const [error,setError]=useState<string|null>(null);
  const [firstIndex,setFirstIndex]=useState(0),[secondIndex,setSecondIndex]=useState(1);
  const [mode,setMode]=useState<"onion"|"difference">("onion");
  const [samplesSignature,setSamplesSignature]=useState<string|null>(null);
  const urls=useRef<string[]>([]),epoch=useRef(0);
  const signature=plan?JSON.stringify(plan):"unavailable";
  useEffect(()=>{
    epoch.current++;urls.current.forEach(URL.revokeObjectURL);urls.current=[];setSamples([]);setSamplesSignature(null);setLoading(false);setError(null);
    return()=>{epoch.current++;urls.current.forEach(URL.revokeObjectURL);urls.current=[];};
  },[signature]);
  const inspect=async()=>{
    if(!plan) return;
    const active=++epoch.current;urls.current.forEach(URL.revokeObjectURL);urls.current=[];
    setSamples([]);setLoading(true);setError(null);
    const pending:Sample[]=[];
    try {
      // Sequential reads bound decoder memory and avoid an eight-request burst at the host.
      for(const selection of plan.samples){
        if(active!==epoch.current) return;
        const bytes=await readNativePreviewFrame(project,selection.token,selection.frameIndex);
        if(bytes.length<24 || bytes.length>16*1024*1024) throw new Error("Native PNG exceeds the bounded inspection size.");
        const copy=new Uint8Array(bytes.length);copy.set(bytes);
        const pngSha256=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",copy.buffer)),b=>b.toString(16).padStart(2,"0")).join("");
        const url=await thumbnail(bytes,plan.width,plan.height);
        if(active!==epoch.current){URL.revokeObjectURL(url);return;}
        urls.current.push(url);pending.push({url,frameIndex:selection.frameIndex,timelineTime:selection.timelineTime,pngSha256,width:plan.width,height:plan.height});
      }
      if(active===epoch.current){setSamples(pending);setSamplesSignature(signature);setFirstIndex(0);setSecondIndex(Math.min(1,pending.length-1));}
    } catch(reason){if(active===epoch.current){urls.current.forEach(URL.revokeObjectURL);urls.current=[];setSamples([]);setError(reason instanceof Error?reason.message:String(reason));}}
    finally {if(active===epoch.current)setLoading(false);}
  };
  const currentSamples=samplesSignature===signature?samples:[];
  const first=currentSamples[firstIndex],second=currentSamples[secondIndex];
  const source=useMemo(()=>nativeFindingSource(project,scene,displayProfile,plan,first),[project,scene,displayProfile,plan,first]);
  const editor=useNativeFindingDraft(project,source,commit,busy),finding=editor.draft.input;
  const sameSource=nativeFindingCurrent(editor.draft,project,source);
  const record=()=>editor.send();
  const draftRepair=()=>{
    if (!plan || !first || !scene || !editor.verifyCurrent()) return;
    try {
      onDraftRepair(createNativeReviewRepairDraft(project,scene,plan,first,finding));
    } catch(reason) {
      setError(reason instanceof Error?reason.message:String(reason));
    }
  };
  return <div className="production-native-inspection">
    <div className="production-section-title"><Eye size={18}/><div><h2>Native frame inspection</h2><p>Motion Canvas v1 · bounded contact sheets, onion frames and evidence-linked review.</p></div></div>
    <div className="production-notice"><strong>Native source frames only</strong><span>No DOM study or placeholder is substituted when the renderer receipt is missing or stale. Thumbnail comparisons are inspection aids, not proof of pixel equivalence or creative quality.</span></div>
    {!plan?<div className="production-empty"><h3>No current native frame grant</h3><p>Select a scene and matching output profile, then produce a native preview in Deliver. Changed revisions, expired grants, different profiles and oversized decode requests are refused.</p><button className="secondary-button" onClick={onOpenRender}>Open Deliver and render preview</button></div>:<>
      <div className="production-button-row"><button className="primary-button" disabled={busy || loading} onClick={inspect}><RefreshCw size={14}/>{loading?"Reading verified native frames…":"Inspect current native frames"}</button><span className="production-help">Up to eight sequential reads · {plan.width} × {plan.height} · source r{plan.revision}</span></div>
      {error && <p className="production-error" role="alert">{error}</p>}
      <div className="native-inspection-grid">{currentSamples.map((sample,index)=><button key={sample.frameIndex} className={firstIndex===index?"selected":""} onClick={()=>{setFirstIndex(index);onSeek(seconds(sample.timelineTime));}} aria-label={`Inspect native frame ${sample.frameIndex}`}>
        <img src={sample.url} alt={`Native frame ${sample.frameIndex}, source r${plan.revision}`} draggable={false}/><span>f{sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s</span><small>SHA-256 {sample.pngSha256.slice(0,12)}…</small></button>)}</div>
      {first && second && <><div className="production-button-row"><label className="production-field"><span>Compare with</span><select value={secondIndex} onChange={e=>setSecondIndex(Number(e.target.value))}>{currentSamples.map((sample,index)=><option key={sample.frameIndex} value={index}>Frame {sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s</option>)}</select></label><label className="production-field"><span>Inspection mode</span><select value={mode} onChange={e=>setMode(e.target.value as "onion"|"difference")}><option value="onion">50% onion overlay</option><option value="difference">Channel difference</option></select></label></div>
        <NativeComparison first={first} second={second} mode={mode}/><p className="production-help">Comparison uses downsampled, color-managed browser thumbnails. Source PNG digests are preserved; zero thumbnail difference is not an assertion of full-resolution equivalence.</p>
        </>}
    </>}
    <section className="native-inspection-finding" aria-label="Native finding author input">
      <h3>Record an actionable finding</h3>
      <p className="production-help">Your finding records the selected target frame only. The comparison frame and display mode are viewing aids and are not attached to the review. Recording creates an open creative review; it never declares a technical or creative PASS.</p>
      {finding.source?<p className="mono" aria-label="Finding source reference">Source r{finding.source.revision} · scene {finding.source.sceneId} · profile {finding.source.profileId} · frame {finding.source.frameIndex} · PNG SHA-256 {finding.source.pngSha256}</p>
        :<p role="status">Inspect a current native frame to select the source for this finding. Your text can be kept before a frame is available.</p>}
      {!sameSource&&finding.source&&<p role="status">The selected native source differs or is unavailable. Your earlier finding remains attached to its original frame.</p>}
      <button className="secondary-button" disabled={!source||editor.busy||nativeFindingUnknown(editor.draft)||sameSource&&!editor.conflict} onClick={editor.chooseCurrent}>Use current frame for this finding</button>
      <div className="production-plan-grid">
        <label className="production-field"><span>Severity</span><select value={finding.severity} onChange={e=>editor.edit("severity",e.target.value as typeof finding.severity)}><option value="blocking">Blocking</option><option value="important">Important</option><option value="suggestion">Suggestion</option></select></label>
        <label className="production-field"><span>Confidence</span><select value={finding.confidence} onChange={e=>editor.edit("confidence",e.target.value as typeof finding.confidence)}><option value="certain">Certain</option><option value="probable">Probable</option><option value="uncertain">Uncertain</option></select></label>
      </div>
      <label className="production-field"><span>Affected object</span><select value={finding.nodeId??""} onChange={e=>editor.edit("nodeId",e.target.value||null)}>
        <option value="">Scene-level finding</option>
        {finding.nodeId&&!scene?.nodes.some(node=>node.id===finding.nodeId)&&<option value={finding.nodeId}>Retained object {finding.nodeId} · outside selected scene</option>}
        {scene?.nodes.map(node=><option key={node.id} value={node.id}>{node.name}</option>)}
      </select></label>
      {([["violatedConstraint","Violated constraint"],["observation","What is visible in this frame"],["proposedRepair","Proposed localized repair"]] as const).map(([field,label])=><label className="production-field" key={field}><span>{label}</span><textarea aria-label={label} rows={3} maxLength={800} value={finding[field]} onChange={e=>editor.edit(field,e.target.value)}/></label>)}
      <div className="production-button-row">
        <button className="primary-button" disabled={editor.blocked||loading||!finding.observation.trim()||!finding.violatedConstraint.trim()||!finding.proposedRepair.trim()} onClick={record}><Flag size={14}/> Record source-anchored review</button>
        <button className="secondary-button" disabled={editor.blocked||loading||!finding.nodeId||!finding.observation.trim()||!finding.violatedConstraint.trim()||!finding.proposedRepair.trim()} onClick={draftRepair}>Draft scoped repair</button>
      </div>
      {editor.session.notice&&<p role="status">{editor.session.notice}</p>}
      {editor.session.error&&<p role="alert">{editor.session.error}</p>}
      {editor.conflict&&<p role="alert">The saved project changed. Inspect its current native frame and explicitly choose that source before a new save. Earlier text stays intact.</p>}
      {editor.session.backupError&&<div role="alert"><p>{editor.session.backupError}</p><button className="secondary-button" disabled={editor.busy} onClick={editor.retry}>Retry finding recovery</button></div>}
      {nativeFindingUnknown(editor.draft)&&<section aria-label="Unconfirmed native finding">
        <h4>Unconfirmed review outcome</h4>
        <p>The original operation may have committed. No request will be repeated automatically. Read the current saved project and consecutive history before a separate authored choice.</p>
        <button className="secondary-button" disabled={editor.busy||editor.reviewing} onClick={editor.readOutcome}>{editor.reviewing?"Reading saved review history…":"Read saved review outcome"}</button>
        {editor.review&&<><p role="status">Read r{editor.review.project.revision}; {editor.review.matchingValueRevisions.length} history changes match the submitted values. This observation does not acknowledge the original response.</p><button className="secondary-button" disabled={editor.busy} onClick={editor.continueEditing}>Keep unknown copy and author a separate finding</button></>}
      </section>}
      <details><summary>Finding recovery copies</summary>
        <p>Local copies retain author input and source references only. They do not contain native access tokens, effect grants or creative acceptance.</p>
        <button className="secondary-button" onClick={()=>editor.download()}>Download current finding input</button>
        {editor.session.limited&&<p role="alert">Only the first bounded set of copies is listed. Preserve the local data before reducing it.</p>}
        <ul>{editor.session.copies.map(copy=><li key={copy.key}>
          <span>{copy.value?"Readable finding input":"Unreadable original bytes"} · {copy.writerId}</span>
          <button className="secondary-button" onClick={()=>editor.download(copy)}>Download copy</button>
          <button className="secondary-button" disabled={editor.busy||nativeFindingUnknown(editor.draft)||!copy.value} onClick={()=>editor.restore(copy)}>Restore separate input</button>
          <button className="secondary-button" disabled={editor.busy||copy.writerId===editor.session.custody.writerId} onClick={()=>editor.discard(copy)}>Discard reviewed local copy</button>
        </li>)}</ul>
      </details>
      <p className="production-help">Drafting a scoped repair selects the affected object and cites this PNG digest. It never invents a replacement, creates a patch or bypasses commit approval.</p>
    </section>
  </div>;
}
