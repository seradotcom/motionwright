import {useState} from "react";
import {CircleCheck,Play} from "lucide-react";
import NativeAvReview from "./NativeAvReview";
import SavedMasterDelivery from "./SavedMasterDelivery";
import type {DeliveryProjectState} from "./deliveryProfileDraft";
import {streamClockIssue,type HybridFailure,type HybridMasterResult,type HybridSourcePreflight} from "./hybridNative";
import type {Project} from "./types";

export type HybridDeliveryAttempt=
  |{status:"dispatching"}
  |{status:"completed";result:HybridMasterResult}
  |{status:"stopped";failure:HybridFailure};

export default function HybridNativeDelivery({
  project,deliverableId,canCheck,canRender,checking,attempt,source,
  runtimeIssue,blocked,currentProjectState,onCheck,onRender,onOpenAudio,
}:{
  project:Project;deliverableId:string;canCheck:boolean;canRender:boolean;checking:boolean;
  attempt:HybridDeliveryAttempt|null;source:HybridSourcePreflight|null;
  runtimeIssue:string|null;blocked:boolean;currentProjectState:()=>DeliveryProjectState;
  onCheck:()=>void;onRender:()=>void;onOpenAudio:()=>void;
}){
  const [reviewKey,setReviewKey]=useState<string|null>(null);
  const result=attempt?.status==="completed"?attempt.result:null;
  const hint=result?.saved_master??null;
  const clockIssue=streamClockIssue(result?.stream_clock);
  const key=hint?JSON.stringify([project.id,project.generation,project.revision,deliverableId,
    hint.mux_receipt_id,hint.master_sha256]):null;
  const reviewing=Boolean(key&&reviewKey===key);
  return <section className="caption-export-panel" aria-label="Native hybrid master">
    <header><div><strong>Native hybrid master</strong>
      <span>Combine Motion Canvas and Manim with the original voice track in one MP4.</span></div></header>
    <p className="delivery-truth-note">
      Use one static or editable Manim scene with adjacent Motion Canvas scenes before it, after it, or both.
      Use contiguous, frame-aligned scenes totaling one to six seconds at 1920 × 1080 and 30 fps.
      The original stereo 48 kHz WAV must match the cut length.
      Save the scene intent and output profile before checking sources.
    </p>
    <div className="delivery-editor-actions">
      <button type="button" className="button compact" disabled={!canCheck} onClick={onCheck}>
        {checking?"Checking hybrid sources...":"Check hybrid sources"}
      </button>
      <button type="button" className="button button-primary" disabled={!canRender} onClick={onRender}>
        <Play size={14}/>{attempt?.status==="dispatching"?"Creating hybrid master...":"Create hybrid master"}
      </button>
      <button type="button" className="button compact" onClick={onOpenAudio}>Review original voice</button>
    </div>
    {source&&<p role="status"><CircleCheck size={14} aria-hidden="true"/>
      {" "}{source.source_count} sources checked for {(source.frame_count/source.fps_num).toFixed(2)} s of video. Creating the master requires your render permission.
    </p>}
    {runtimeIssue&&<p className="delivery-truth-note">{runtimeIssue}</p>}
    {attempt?.status==="dispatching"&&<p role="status">
      The original operation is in progress. Its result will remain in Jobs and saved masters.
    </p>}
    {attempt?.status==="stopped"&&<p role="alert">
      {attempt.failure.status==="unknown"?"Original outcome is unknown. ":"The original operation stopped. "}
      {attempt.failure.message} Rendering this selection again is blocked; inspect the original operation in Jobs.
    </p>}
    {result&&<>
      <div className="portable-plan" aria-label="Completed original hybrid master" role="status">
        <div><span>Saved source</span><strong>r{result.revision}</strong></div>
        <div><span>Master</span><strong>{(result.frame_count/result.fps_num).toFixed(2)} s · H.264/AAC</strong></div>
        <div><span>Original SHA-256</span><code>{result.master_sha256}</code></div>
      </div>
      {result.review_warning&&<p role="status">{result.review_warning}</p>}
      {hint&&<button type="button" className="button compact" onClick={()=>setReviewKey(reviewing?null:key)}>
        {reviewing?"Close hybrid original review":"Review completed hybrid original"}
      </button>}
      {!hint&&<p>Use “Find saved MP4s” below to reopen this original.</p>}
      {clockIssue
        ? !reviewing && <p className="delivery-truth-note" role="status"><strong>Review only.</strong> {clockIssue}</p>
        : <p className="delivery-truth-note">Review the picture and sound before delivery. Export requires a separate destination permission.</p>}
    </>}
    {reviewing&&hint&&<>
      <NativeAvReview project={project} muxReceiptHint={hint} deliverableId={deliverableId}
        masterSha256={hint.master_sha256} embedded initialHybridClock={result?.stream_clock}
        savedDelivery={<SavedMasterDelivery key={key!} project={project} deliverableId={deliverableId} hint={hint}
          blocked={blocked} currentProjectState={currentProjectState}/>}/>
    </>}
  </section>;
}
