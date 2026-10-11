import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, Flag, RefreshCw } from "lucide-react";
import { readNativePreviewFrame } from "./api";
import { addFrameOffset, nativeFindingBody, planNativeInspection } from "./nativeInspection";
import { createNativeReviewRepairDraft } from "./nativeReviewRepair";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import type { ManualCreativeFinding, NativeSampleEvidence } from "./nativeInspection";
import type { Change, DeliverableProfile, MotionCanvasRenderEvidence, Project, Scene } from "./types";
import { seconds } from "./types";

interface Sample extends NativeSampleEvidence { url:string; }
const defaultFinding:ManualCreativeFinding={severity:"important",confidence:"probable",violatedConstraint:"",observation:"",proposedRepair:"",nodeId:null};

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
  commit:(change:Change)=>Promise<void>;busy:boolean;onSeek:(time:number)=>void;onOpenRender:()=>void;
  onDraftRepair:(draft:NativeReviewRepairDraft)=>void;
}) {
  const plan=useMemo(()=>planNativeInspection(project,scene,displayProfile,evidence),[project,scene,displayProfile,evidence]);
  const [samples,setSamples]=useState<Sample[]>([]);const [loading,setLoading]=useState(false);const [error,setError]=useState<string|null>(null);
  const [firstIndex,setFirstIndex]=useState(0),[secondIndex,setSecondIndex]=useState(1);
  const [mode,setMode]=useState<"onion"|"difference">("onion");const [finding,setFinding]=useState<ManualCreativeFinding>(defaultFinding);
  const urls=useRef<string[]>([]),epoch=useRef(0);
  const signature=plan?`${plan.generation}:${plan.revision}:${plan.sceneId}:${plan.profileId}:${plan.samples[0]?.token}`:"unavailable";
  useEffect(()=>{
    epoch.current++;urls.current.forEach(URL.revokeObjectURL);urls.current=[];setSamples([]);setLoading(false);setError(null);
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
      if(active===epoch.current){setSamples(pending);setFirstIndex(0);setSecondIndex(Math.min(1,pending.length-1));}
    } catch(reason){if(active===epoch.current){urls.current.forEach(URL.revokeObjectURL);urls.current=[];setSamples([]);setError(reason instanceof Error?reason.message:String(reason));}}
    finally {if(active===epoch.current)setLoading(false);}
  };
  const first=samples[firstIndex],second=samples[secondIndex];
  const draftRepair=()=>{
    if (!plan || !first || !scene) return;
    try {
      onDraftRepair(createNativeReviewRepairDraft(project,scene,plan,first,finding));
    } catch(reason) {
      setError(reason instanceof Error?reason.message:String(reason));
    }
  };
  const record=async()=>{
    if(!plan || !first || !scene) return;
    try {
      if(finding.nodeId && !scene.nodes.some(n=>n.id===finding.nodeId)) throw new Error("Finding target is outside this scene.");
      await commit({type:"add_review",kind:"creative",resource:"scene:"+scene.id,body:nativeFindingBody(plan,first,finding),
        start:first.timelineTime,end:addFrameOffset(first.timelineTime,1,plan.frameRate),locale:null,profile_id:plan.profileId});
    } catch(reason){setError(reason instanceof Error?reason.message:String(reason));}
  };
  return <div className="production-native-inspection">
    <div className="production-section-title"><Eye size={18}/><div><h2>Native frame inspection</h2><p>Motion Canvas v1 · bounded contact sheets, onion frames and evidence-linked review.</p></div></div>
    <div className="production-notice"><strong>Native source frames only</strong><span>No DOM study or placeholder is substituted when the renderer receipt is missing or stale. Thumbnail comparisons are inspection aids, not proof of pixel equivalence or creative quality.</span></div>
    {!plan?<div className="production-empty"><h3>No current native frame grant</h3><p>Select a scene and matching output profile, then produce a native preview in Deliver. Changed revisions, expired grants, different profiles and oversized decode requests are refused.</p><button className="secondary-button" onClick={onOpenRender}>Open Deliver and render preview</button></div>:<>
      <div className="production-button-row"><button className="primary-button" disabled={busy || loading} onClick={inspect}><RefreshCw size={14}/>{loading?"Reading verified native frames…":"Inspect current native frames"}</button><span className="production-help">Up to eight sequential reads · {plan.width} × {plan.height} · source r{plan.revision}</span></div>
      {error && <p className="production-error" role="alert">{error}</p>}
      <div className="native-inspection-grid">{samples.map((sample,index)=><button key={sample.frameIndex} className={firstIndex===index?"selected":""} onClick={()=>{setFirstIndex(index);onSeek(seconds(sample.timelineTime));}} aria-label={`Inspect native frame ${sample.frameIndex}`}>
        <img src={sample.url} alt={`Native frame ${sample.frameIndex}, source r${plan.revision}`} draggable={false}/><span>f{sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s</span><small>SHA-256 {sample.pngSha256.slice(0,12)}…</small></button>)}</div>
      {first && second && <><div className="production-button-row"><label className="production-field"><span>Compare with</span><select value={secondIndex} onChange={e=>setSecondIndex(Number(e.target.value))}>{samples.map((sample,index)=><option key={sample.frameIndex} value={index}>Frame {sample.frameIndex} · {seconds(sample.timelineTime).toFixed(3)} s</option>)}</select></label><label className="production-field"><span>Inspection mode</span><select value={mode} onChange={e=>setMode(e.target.value as "onion"|"difference")}><option value="onion">50% onion overlay</option><option value="difference">Channel difference</option></select></label></div>
        <NativeComparison first={first} second={second} mode={mode}/><p className="production-help">Comparison uses downsampled, color-managed browser thumbnails. Source PNG digests are preserved; zero thumbnail difference is not an assertion of full-resolution equivalence.</p>
        <section className="native-inspection-finding"><h3>Record an actionable finding</h3><p className="production-help">This is a manual interpretation of the selected source frame. Recording it creates a revisioned review thread and makes the current frame grant stale. Drafting a repair only opens the scoped editor; neither action declares a technical or creative PASS.</p>
          <div className="production-plan-grid"><label className="production-field"><span>Severity</span><select value={finding.severity} onChange={e=>setFinding({...finding,severity:e.target.value as ManualCreativeFinding["severity"]})}><option value="blocking">Blocking</option><option value="important">Important</option><option value="suggestion">Suggestion</option></select></label><label className="production-field"><span>Confidence</span><select value={finding.confidence} onChange={e=>setFinding({...finding,confidence:e.target.value as ManualCreativeFinding["confidence"]})}><option value="certain">Certain</option><option value="probable">Probable</option><option value="uncertain">Uncertain</option></select></label></div>
          <label className="production-field"><span>Affected object</span><select value={finding.nodeId ?? ""} onChange={e=>setFinding({...finding,nodeId:e.target.value || null})}><option value="">Scene-level finding</option>{scene?.nodes.map(n=><option key={n.id} value={n.id}>{n.name}</option>)}</select></label>
          {([ ["violatedConstraint","Violated constraint"],["observation","What is visible in this frame"],["proposedRepair","Proposed localized repair"] ] as const).map(([field,label])=><label className="production-field" key={field}><span>{label}</span><textarea rows={3} maxLength={800} value={finding[field]} onChange={e=>setFinding({...finding,[field]:e.target.value})}/></label>)}
          <div className="production-button-row">
            <button className="primary-button" disabled={busy || loading || !finding.observation.trim() || !finding.violatedConstraint.trim() || !finding.proposedRepair.trim()} onClick={record}><Flag size={14}/> Record source-anchored review</button>
            <button className="secondary-button" disabled={busy || loading || !finding.nodeId || !finding.observation.trim() || !finding.violatedConstraint.trim() || !finding.proposedRepair.trim()} onClick={draftRepair}>Draft scoped repair</button>
          </div>
          <p className="production-help">Drafting selects the affected object and cites this frame SHA-256. It does not invent a replacement, create a patch, or bypass the existing commit approval.</p>
        </section></>}
    </>}
  </div>;
}
