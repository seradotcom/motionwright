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
}:{
  cues:NativeSfxAuditionCue[];
  masterFrames:number;
  mediaSeconds:number|null;
  playbackObserved:boolean;
  onSeek:(timeSeconds:number)=>void;
}) {
  const [notes,setNotes]=useState<Record<string,SessionNote>>({});
  const reviewed=cues.filter(cue=>notes[cue.cueId]!==undefined).length;
  const needsAdjustment=cues.filter(cue=>notes[cue.cueId]==="needs-adjustment").length;
  const activeFrame=mediaSeconds===null ? null
    : Math.max(0,Math.floor(mediaSeconds*30));

  const mark=(id:string,value:SessionNote)=>{
    if(!playbackObserved)return;
    setNotes(previous=>({...previous,[id]:value}));
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
          they are <strong>not persisted, certified or creative approval</strong>.
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
          onClick={()=>setNotes({})}>
          <RotateCcw size={13} aria-hidden="true"/>
          Clear session notes
        </button>
      </footer>
    </section>
  );
}
