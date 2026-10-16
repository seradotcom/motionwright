import { Check, CircleDashed, RotateCcw, Scissors, Volume2 } from "lucide-react";
import { useState } from "react";
import type { NativeSfxAuditionCue } from "./nativeSfxAudition";
import { sourceFrameLabel } from "./nativeSfxAudition";

type SessionNote = "aligned" | "needs-adjustment";

/** Manual, ephemeral listening annotations. No project mutation, server
 * sync, technical receipt or creative approval is ever implied.
 */
export default function NativeSfxCueAudition({
  cues, masterFrames, mediaSeconds, playbackObserved, onSeek,
  onRecordIssue, issueBusy = false,
}:{
  cues:NativeSfxAuditionCue[];
  masterFrames:number;
  mediaSeconds:number|null;
  playbackObserved:boolean;
  onSeek:(timeSeconds:number)=>void;
  /** Explicit manual CAS review creation, never an automatic approval. */
  onRecordIssue?:(cue:NativeSfxAuditionCue,observation:string)=>Promise<void>;
  issueBusy?:boolean;
}) {
  const [notes,setNotes]=useState<Record<string,SessionNote>>({});
  const [observations,setObservations]=useState<Record<string,string>>({});
  const [recordingCue,setRecordingCue]=useState<string|null>(null);
  const reviewed=cues.filter(cue=>notes[cue.cueId]!==undefined).length;
  const needsAdjustment=cues.filter(cue=>notes[cue.cueId]==="needs-adjustment").length;
  const activeFrame=mediaSeconds===null ? null
    : Math.max(0,Math.floor(mediaSeconds*30));

  const mark=(id:string,value:SessionNote)=>{
    if(!playbackObserved)return;
    setNotes(previous=>({...previous,[id]:value}));
  };
  const record=async(cue:NativeSfxAuditionCue)=>{
    if(!playbackObserved || notes[cue.cueId]!=="needs-adjustment"
      || !onRecordIssue || recordingCue || issueBusy)return;
    const observation=(observations[cue.cueId]??"").trim();
    if(observation.length<10 || [...observation].length>640)return;
    setRecordingCue(cue.cueId);
    try {
      await onRecordIssue(cue,observation);
    } finally {
      setRecordingCue(null);
    }
  };
  return (
    <section className="sfx-audition" role="region"
      aria-label="Original semantic SFX listening review">
      <header className="sfx-audition-heading">
        <div>
          <strong>Listen at the cue</strong>
          <span>Original source positions in the verified native MP4</span>
        </div>
        <div className="sfx-audition-count">
          <strong>{reviewed} / {cues.length} noted</strong>
          <span>{needsAdjustment} need adjustment</span>
        </div>
      </header>
      <div className="sfx-audition-notice">
        <Volume2 size={15} aria-hidden="true"/>
        <span>
          Play the MP4, then seek to each authored sound and record a subjective
          alignment note. These notes exist only in this review session:
          session labels are <strong>not persisted, certified or creative approval</strong>.
          Only the separate “Record open creative review” action saves a human
          issue to the current project, invalidating this native preview.
        </span>
      </div>
      {cues.length>0 && (
        <div className="sfx-audition-cues" role="list">
          {cues.map((cue,i)=>{
            const active=activeFrame!==null && activeFrame>=cue.atFrame
              && activeFrame<cue.endFrame;
            const choice=notes[cue.cueId];
            const left=Math.max(0,100*cue.atFrame/masterFrames);
            const width=Math.min(100-left,100*cue.durationFrames/masterFrames);
            return (
              <div className="sfx-audition-cue" role="listitem" key={cue.cueId}
                data-status={choice??"unreviewed"} data-playing={active}>
                <div className="sfx-audition-cue-heading">
                  <div className="sfx-audition-cue-title">
                    <span className="mono">{String(i+1).padStart(2,"0")}</span>
                    <strong>{cue.event}</strong>
                    <span>{cue.sceneName}</span>
                  </div>
                  <button type="button" className="button compact"
                    aria-label={"Seek verified MP4 to "+cue.event+" at frame "+cue.atFrame}
                    onClick={()=>onSeek(cue.atSeconds)}>
                    <Volume2 size={13} aria-hidden="true"/>
                    Seek {sourceFrameLabel(cue.atFrame)}
                  </button>
                </div>
                <div className="sfx-audition-ruler" aria-hidden="true">
                  <div className="sfx-audition-ruler-fill"
                    style={{left:left+"%",width:width+"%"}}/>
                  {activeFrame!==null && activeFrame<=masterFrames && (
                    <div className="sfx-audition-ruler-head"
                      style={{left:(100*activeFrame/masterFrames)+"%"}}/>
                  )}
                </div>
                <div className="sfx-audition-cue-metadata">
                  <span>Frame {cue.atFrame}–{cue.endFrame} · 48 kHz sample {cue.absolutePcmSample.toLocaleString("en-US")}</span>
                  <span className="mono">WAV {cue.originalSourceDigest.slice(0,12)}…</span>
                </div>
                <div className="sfx-audition-decisions">
                  <button type="button" className="button compact"
                    disabled={!playbackObserved}
                    aria-pressed={choice==="aligned"}
                    onClick={()=>mark(cue.cueId,"aligned")}>
                    <Check size={13} aria-hidden="true"/>
                    Sounds aligned
                  </button>
                  <button type="button" className="button compact"
                    disabled={!playbackObserved}
                    aria-pressed={choice==="needs-adjustment"}
                    onClick={()=>mark(cue.cueId,"needs-adjustment")}>
                    <Scissors size={13} aria-hidden="true"/>
                    Needs adjustment
                  </button>
                  {choice===undefined && (
                    <span className="sfx-audition-pending">
                      <CircleDashed size={12} aria-hidden="true"/>
                      {playbackObserved?"Not assessed":"Play media before marking"}
                    </span>
                  )}
                </div>
                {choice==="needs-adjustment" && onRecordIssue && (
                  <div className="sfx-audition-record">
                    <label>
                      <span className="field-label">What sounded wrong at this cue?</span>
                      <textarea rows={2} maxLength={640}
                        aria-label={"Original SFX listening issue for "+cue.event}
                        placeholder="Describe the audible mismatch and what needs to change"
                        value={observations[cue.cueId]??""}
                        disabled={!playbackObserved || issueBusy || recordingCue!==null}
                        onChange={(event)=>setObservations(prev=>({
                          ...prev,[cue.cueId]:event.target.value,
                        }))}/>
                    </label>
                    <button className="button compact" type="button"
                      aria-label={"Record open creative review for "+cue.event}
                      disabled={!playbackObserved || issueBusy || recordingCue!==null
                        || (observations[cue.cueId]??"").trim().length<10}
                      onClick={()=>void record(cue)}>
                      {recordingCue===cue.cueId?"Recording review…":"Record open creative review"}
                    </button>
                    <span>Explicit project edit; the native source preview becomes historical.
                      No audio is changed or approved automatically.</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <footer className="sfx-audition-footer">
        <span>{reviewed===cues.length
          ? "All cues have session notes; human creative approval is still required."
          : String(cues.length-reviewed)
            + (cues.length-reviewed===1 ? " cue awaiting" : " cues awaiting")
            + " a listener's session note."}</span>
        <button type="button" className="button compact"
          disabled={reviewed===0}
          onClick={()=>{setNotes({});setObservations({});}}>
          <RotateCcw size={13} aria-hidden="true"/>
          Clear session notes
        </button>
      </footer>
    </section>
  );
}
