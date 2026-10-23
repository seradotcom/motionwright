import { CircleDashed, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { readNativePreviewFrame } from "./api";
import type { NativeFrameSelection } from "./nativeFrameSelection";
import {
  NativePreviewByteCache, planNativePreviewRead,
  previewTier, requireBoundedNativePng,
} from "./nativeProgressivePreview";
import type { Project } from "./types";
import type { DecodedPreviewFrame } from "./previewRecovery";

interface PresentedFrame { frameIndex: number; url: string; scope: string; bytes: Uint8Array }
interface FrameProblem { frameIndex: number; message: string }

function imageUrl(bytes: Uint8Array): string {
  const copy=new Uint8Array(bytes.length);
  copy.set(bytes);
  return URL.createObjectURL(new Blob([copy.buffer], {type:"image/png"}));
}

/** Progressive REQUEST CADENCE, not invented low-res media:
 * while scrubbing we sample source-verified native PNGs at up to 4Hz,
 * on pause we settle on the EXACT requested frame. At most one readback
 * runs per mounted source scope, with a bounded three-frame byte cache.
 * An old verified frame can be held but must never be labeled current.
 */
export default function NativeFrameStage({
  project,selection,editorialPlaying,onDecoded,
}:{
  project:Project;
  selection:NativeFrameSelection;
  editorialPlaying:boolean;
  onDecoded?:(frame:DecodedPreviewFrame)=>void;
}) {
  const scope=[
    project.id,project.generation,project.active_branch,project.revision,selection.token,selection.segmentId,
  ].join(":");
  const activeScope=useRef(scope);
  activeScope.current=scope;
  const decodedCallback=useRef(onDecoded);
  decodedCallback.current=onDecoded;
  const [presented,setPresented]=useState<PresentedFrame|null>(null);
  const [failure,setFailure]=useState<FrameProblem|null>(null);
  const [reading,setReading]=useState(false);
  const [retry,setRetry]=useState(0);
  const cache=useRef(new NativePreviewByteCache());
  const latestFrame=useRef<PresentedFrame|null>(null);
  const currentlyRequested=useRef({
    frameIndex:selection.frameIndex,
    playing:editorialPlaying,
    changedAt:performance.now(),
  });
  const current=currentlyRequested.current;
  if(current.frameIndex!==selection.frameIndex||current.playing!==editorialPlaying) {
    currentlyRequested.current={
      frameIndex:selection.frameIndex,
      playing:editorialPlaying,
      changedAt:performance.now(),
    };
  }
  const retryRef=useRef(0);
  const failedFrame=useRef<number|null>(null);
  if(retryRef.current!==retry){
    retryRef.current=retry;
    failedFrame.current=null;
    currentlyRequested.current.changedAt=performance.now()-130;
  }

  useEffect(()=>{
    let disposed=false;
    let inFlight=false;
    let lastStarted=-Infinity;
    let currentUrl:string|null=null;
    cache.current.clear();
    latestFrame.current=null;
    setPresented(null);
    setFailure(null);
    setReading(false);
    failedFrame.current=null;

    const publish=(index:number,bytes:Uint8Array)=>{
      if(disposed)return;
      const url=imageUrl(bytes);
      const prior=currentUrl;
      currentUrl=url;
      const ready={frameIndex:index,url,scope,bytes:new Uint8Array(bytes)};
      latestFrame.current=ready;
      setPresented(ready);
      setFailure(null);
      // A single earlier URL remains valid through the React image commit.
      // The bounded timeout revokes it even if the component is subsequently removed.
      if(prior)window.setTimeout(()=>URL.revokeObjectURL(prior),120);
    };
    const tick=()=>{
      if(disposed)return;
      const desired=currentlyRequested.current;
      const visible=latestFrame.current?.scope===scope
        ?latestFrame.current.frameIndex:null;
      const cached=cache.current.has(desired.frameIndex);
      const decision=planNativePreviewRead({
        desiredFrame:desired.frameIndex,
        showingFrame:visible,
        playing:desired.playing,
        inFlight,
        failedFrame:failedFrame.current,
        cacheHit:cached,
        nowMs:performance.now(),
        targetChangedAtMs:desired.changedAt,
        lastRequestStartedAtMs:lastStarted,
      });
      if(decision==="cache"){
        const bytes=cache.current.get(desired.frameIndex);
        if(bytes)publish(desired.frameIndex,bytes);
      }
      if(decision!=="fetch")return;
      inFlight=true;
      const index=desired.frameIndex;
      lastStarted=performance.now();
      setReading(true);
      void readNativePreviewFrame(project,selection.token,index)
        .then(bytes=>{
          requireBoundedNativePng(bytes);
          if(disposed)return;
          cache.current.put(index,bytes);
          if(currentlyRequested.current.frameIndex===index)
            publish(index,bytes);
        })
        .catch(reason=>{
          if(disposed)return;
          failedFrame.current=index;
          if(currentlyRequested.current.frameIndex===index)
            setFailure({frameIndex:index,message:reason instanceof Error?reason.message:
              "Native frame readback failed."});
        })
        .finally(()=>{
          inFlight=false;
          if(!disposed)setReading(false);
        });
    };
    // Transport may update at 10Hz; polling does no I/O unless the
    // source-bound pure policy admits a new frame request.
    // Do not issue I/O synchronously during effect setup: React StrictMode
    // replays mount effects in development. The first 45ms tick prevents
    // duplicate concurrent readbacks across that lifecycle boundary.
    const timer=window.setInterval(tick,45);
    return ()=>{
      disposed=true;
      latestFrame.current=null;
      window.clearInterval(timer);
      cache.current.clear();
      if(currentUrl)URL.revokeObjectURL(currentUrl);
    };
  },[scope,project.id,project.generation,project.active_branch,project.revision,selection.token,selection.segmentId]);

  const ready=presented?.scope===scope?presented:null;
  const exact=ready?.frameIndex===selection.frameIndex;
  const visibleFailure=failure?.frameIndex===selection.frameIndex?failure:null;
  const requested="Frame "+(selection.frameIndex+1)+" / "+selection.frameCount;
  const actual=ready?"Frame "+(ready.frameIndex+1)+" / "+selection.frameCount:null;
  return <div className="native-frame-stage" role="region" aria-label="Verified native PNG frame preview">
    {ready&&<img className="native-frame-image" src={ready.url}
      alt={"Verified native rendered PNG · "+actual}
      draggable={false}
      onLoad={event=>{
        if(activeScope.current!==ready.scope||latestFrame.current?.url!==ready.url)return;
        const image=event.currentTarget;
        if(image.naturalWidth<1||image.naturalHeight<1)return;
        decodedCallback.current?.({bytes:new Uint8Array(ready.bytes),frameIndex:ready.frameIndex,
          frameCount:selection.frameCount,pixelWidth:image.naturalWidth,pixelHeight:image.naturalHeight});
      }}
      onError={()=>{
        if(latestFrame.current?.url!==ready.url)return;
        failedFrame.current=ready.frameIndex;
        cache.current.delete(ready.frameIndex);
        latestFrame.current=null;
        URL.revokeObjectURL(ready.url);
        setPresented(null);
        setFailure({
          frameIndex:ready.frameIndex,
          message:"The decoded PNG cannot be displayed by this WebView.",
        });
      }}/>}
    {!ready && !visibleFailure && <div className="native-frame-loading" role="status">
      <CircleDashed size={21} aria-hidden="true"/>
      <span>{reading?"Reading source-verified native PNG…":"Settling on exact native frame…"}</span>
    </div>}
    {visibleFailure&&<div className="native-frame-error" role="alert">
      <TriangleAlert size={20} aria-hidden="true"/>
      <strong>Native frame readback unavailable</strong>
      <span>{visibleFailure.message}</span>
      <button type="button" className="button compact"
        onClick={()=>{setFailure(null);setRetry(r=>r+1);}}>
        <RefreshCw size={13} aria-hidden="true"/> Retry this frame
      </button>
    </div>}
    {ready&&!exact&&<div className="native-frame-held" role="status">
      Holding previously verified {actual}; requested {requested}.
      The held pixels are not the current transport frame.
    </div>}
    <div className="native-frame-caption">
      <span>{exact?actual??requested:requested} · source r{project.revision}</span>
      <span>{previewTier(editorialPlaying)==="sampled_scrub"
        ?"Sampled source PNG during scrub · up to 4 reads/s"
        :"Settled exact source PNG · no synthesized frames"}
      </span>
    </div>
  </div>;
}
