import { useEffect, useMemo, useState } from "react";
import { AudioLines, MoveRight, ShieldAlert } from "lucide-react";
import MusicConformWorkbench from "./MusicConformWorkbench";
import type { Change, Project, RationalTime } from "./types";
import { seconds } from "./types";
import type { ProductionClock, ProductionPlan } from "./creativeProduction";
import {
  formatMarkedBeat,
  generateAuthorBeatGrid,
  inspectMusicCut,
  parseMarkedBeatGrid,
} from "./musicClock";

type MusicClock = Extract<ProductionClock,{kind:"music"}>;
type Props = {
  project: Project;
  draftClock: MusicClock;
  storedPlan: ProductionPlan | null | undefined;
  draftMatchesSaved: boolean;
  baseRevision: number;
  busy: boolean;
  onClockChange: (clock: MusicClock) => void;
  commit: (change: Change) => Promise<void>;
  onSeek: (seconds: number) => void;
};

function readableTime(time: RationalTime): string {
  const value = seconds(time);
  return Number.isFinite(value) ? value.toFixed(3) + "s" : "invalid";
}
function markedText(beats: RationalTime[]): string {
  try { return beats.map(formatMarkedBeat).join("\n"); }
  catch { return ""; }
}

/** A music-clock authoring UI, NOT a BPM detector or a DAW.
 * Native authority is always the revision-scoped change through Semwright SDK.
 */
export default function MusicClockWorkbench({
  project,draftClock,storedPlan,draftMatchesSaved,baseRevision,busy,
  onClockChange,commit,onSeek,
}: Props) {
  const [marked,setMarked]=useState(()=>markedText(draftClock.beats));
  const [bpm,setBpm]=useState("120");
  const [count,setCount]=useState("24");
  const [offset,setOffset]=useState("0.000");
  const [markError,setMarkError]=useState<string|null>(null);
  const [cutSceneId,setCutSceneId]=useState(()=>project.scenes[0]?.id ?? "");
  const [cutBeatIndex,setCutBeatIndex]=useState(0);
  const [acknowledge,setAcknowledge]=useState(false);
  const [cutError,setCutError]=useState<string|null>(null);
  const [cutNote,setCutNote]=useState<string|null>(null);

  const sources=project.assets.filter(asset=>asset.media_type.startsWith("audio/") && /^[0-9a-f]{64}$/.test(asset.content_sha256 ?? ""));
  const selected=sources.find(asset=>asset.id===draftClock.asset_id);
  useEffect(()=>{
    setMarked(markedText(draftClock.beats));
    setMarkError(null);
  },[draftClock.asset_id, draftClock.beats]);
  useEffect(()=>{setAcknowledge(false);setCutError(null);setCutNote(null);},
    [cutSceneId,cutBeatIndex,project.revision]);
  const savedClock=storedPlan?.clock.kind==="music" ? storedPlan.clock : null;
  const proposalResult=useMemo(()=>{
    if (!savedClock || !draftMatchesSaved) return {proposal:null,error:"Save this exact music clock to the production plan before retiming."};
    try {
      return {proposal:inspectMusicCut(project,cutSceneId,cutBeatIndex),error:null};
    } catch (error) {
      return {proposal:null,error:error instanceof Error?error.message:String(error)};
    }
  },[project,cutSceneId,cutBeatIndex,savedClock,draftMatchesSaved]);
  const proposal=proposalResult.proposal;
  const locked=baseRevision!==project.revision || busy;

  const replaceMarks = () => {
    try {
      const beats=parseMarkedBeatGrid(marked);
      onClockChange({...draftClock,beats});
      setMarkError(null);
      setCutNote(null);
    } catch (error) {
      setMarkError(error instanceof Error?error.message:String(error));
    }
  };
  const generateMarks = () => {
    try {
      const [start] = parseMarkedBeatGrid(offset);
      const beats=generateAuthorBeatGrid(Number(bpm),Number(count),start);
      onClockChange({...draftClock,beats});
      setMarked(markedText(beats));
      setMarkError(null);
      setCutNote(null);
    } catch (error) {
      setMarkError(error instanceof Error?error.message:String(error));
    }
  };
  const snapCut = async () => {
    if (!proposal || locked || !draftMatchesSaved || (proposal.anchors_need_acknowledgement && !acknowledge)) return;
    setCutError(null);
    try {
      await commit({
        type:"snap_scene_cut_to_music",
        scene_id:proposal.scene_id,
        beat_index:proposal.beat_index,
        source_sha256:proposal.source_sha256,
        base_revision:project.revision,
        acknowledge_absolute_anchors:acknowledge,
      });
      setCutNote("Applied one scoped cut. Downstream scene starts reflowed. Existing cues and markers were not moved; review them against the new shot boundaries.");
      setAcknowledge(false);
    } catch (error) {
      setCutError(error instanceof Error?error.message:String(error));
    }
  };

  return <section className="music-clock-workbench" aria-label="Music beat clock and cut review">
    <header className="music-clock-heading"><AudioLines size={17} aria-hidden="true"/><div>
      <h3>Musical edit clock</h3>
      <p>Source-bound, author-marked beat grid · exact rational seconds</p>
    </div></header>
    <p className="production-help">Import your authorized audio first. Mark beats yourself, or draft a grid from a declared tempo. This editor does not listen to the audio, measure onset times, validate music rights or retime the soundtrack.</p>
    <label className="production-field"><span>Source audio asset</span>
      <select aria-label="Music source asset" value={selected?.id ?? ""} onChange={event=>{
        onClockChange({...draftClock,asset_id:event.target.value});setCutNote(null);
      }}>
        <option value="">Choose an imported digest-bound audio file</option>
        {sources.map(asset=><option key={asset.id} value={asset.id}>{asset.name}</option>)}
      </select>
    </label>
    {selected && <p className="music-clock-source" aria-label="Selected audio fingerprint">
      Asset SHA-256 <code>{selected.content_sha256?.slice(0,16)}…</code> · an asset hash is not an independent musical timing measurement
    </p>}
    <div className="music-clock-grid">
      <label className="production-field music-clock-marks"><span>Author-marked beat times · one per line, seconds.mmm or exact n/d</span>
        <textarea aria-label="Music beat timestamps" rows={6} value={marked} spellCheck={false} onChange={event=>setMarked(event.target.value)} placeholder={"0.000\n0.500\n1.000\n1.500"}/>
        <small>1–512 strictly increasing beat boundaries per authored edit. Inexact decimals are never rounded silently.</small>
      </label>
      <div className="music-clock-generator"><strong>Generate a timing draft</strong>
        <label className="production-field"><span>Declared BPM</span><input aria-label="Declared beats per minute" type="number" min={30} max={300} step={1} value={bpm} onChange={e=>setBpm(e.target.value)}/></label>
        <label className="production-field"><span>Beat count</span><input aria-label="Generated beat count" type="number" min={1} max={512} step={1} value={count} onChange={e=>setCount(e.target.value)}/></label>
        <label className="production-field"><span>Start offset · seconds.mmm or n/d</span><input aria-label="Generated grid offset" value={offset} onChange={e=>setOffset(e.target.value)}/></label>
        <button type="button" className="secondary-button" disabled={locked} onClick={generateMarks}>Generate exact draft</button>
      </div>
    </div>
    <div className="production-button-row">
      <button type="button" className="secondary-button" disabled={locked || !selected} onClick={replaceMarks}>Use marked times in plan draft</button>
      <span className="mono">{draftClock.beats.length} draft beat{draftClock.beats.length===1?"":"s"}</span>
    </div>
    {markError && <p className="production-error" role="alert">{markError}</p>}
    <p className="production-help">The beat grid is stored only when you select <strong>Save production plan</strong> below. Any plan-content edit clears a previous concept approval; no native render or audio effect occurs.</p>

    <div className="music-cut-review">
      <header className="music-clock-heading"><MoveRight size={17} aria-hidden="true"/><div>
        <h3>Snap one editorial cut</h3>
        <p>Review downstream shifts before a single revisioned project mutation</p>
      </div></header>
      <div className="production-plan-grid">
        <label className="production-field"><span>Scene to retime</span>
          <select aria-label="Music cut target scene" value={cutSceneId} onChange={e=>setCutSceneId(e.target.value)}>
            {project.scenes.map(scene=><option key={scene.id} value={scene.id}>{scene.name}</option>)}
          </select>
        </label>
        <label className="production-field"><span>Saved beat boundary</span>
          <select aria-label="Music cut target beat" value={Math.min(cutBeatIndex,Math.max(0,(savedClock?.beats.length??1)-1))}
            onChange={e=>setCutBeatIndex(Number(e.target.value))}>
            {(savedClock?.beats ?? []).slice(0,512).map((beat,i)=>
              <option key={i} value={i}>{String(i+1).padStart(3,"0")} · {readableTime(beat)}</option>)}
          </select>
        </label>
      </div>
      {proposal && <div className="music-cut-impact" aria-label="Music cut impact preview">
        <div><span>Current cut</span><strong>{readableTime(proposal.original_end)}</strong></div>
        <div><span>Proposed cut</span><strong>{readableTime(proposal.target_end)}</strong></div>
        <div><span>Time ripple</span><strong>{readableTime(proposal.ripple)}</strong></div>
        <div><span>Following scenes shifted</span><strong>{proposal.downstream_scene_ids.length}</strong></div>
        <div><span>Absolute cues / markers / transcript segments</span><strong>{proposal.affected_cue_count} / {proposal.affected_marker_count} / {proposal.affected_transcript_count}</strong></div>
      </div>}
      {proposalResult.error && <p className="production-help" role="status">{proposalResult.error}</p>}
      {proposal && <div className="music-cut-footnote"><ShieldAlert size={17} aria-hidden="true"/>
        <p>Scenes after this cut move by the exact ripple. Authored keyframes stay at their original scene-local times. Existing audio cues, markers and transcript timestamps remain fixed to the global timeline. The scene returns to Draft; any recorded plan approval is cleared. This is not an audio remix or renderer verification.</p>
      </div>}
      {proposal?.anchors_need_acknowledgement && <label className="production-check music-cut-acknowledge">
        <input type="checkbox" aria-label="Acknowledge fixed audio and marker times" checked={acknowledge} onChange={e=>setAcknowledge(e.target.checked)}/>
        I understand existing absolute-time cues, markers and transcript segments will not move; I will inspect those alignments after this cut.
      </label>}
      <div className="production-button-row">
        <button type="button" className="primary-button" disabled={locked || !proposal || (proposal.anchors_need_acknowledgement && !acknowledge)}
          onClick={()=>void snapCut()}>Apply this scoped musical cut</button>
        <button type="button" className="secondary-button" disabled={!proposal} onClick={()=>proposal && onSeek(seconds(proposal.target_end))}>
          Preview cut boundary
        </button>
      </div>
      {cutError && <p className="production-error" role="alert">{cutError}</p>}
      {cutNote && <p className="production-help" role="status">{cutNote}</p>}
    </div>
    <MusicConformWorkbench project={project} savedClock={savedClock}
      draftMatchesSaved={draftMatchesSaved} baseRevision={baseRevision}
      busy={busy} commit={commit} onSeek={onSeek}/>
  </section>;
}
