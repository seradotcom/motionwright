import { useEffect, useMemo, useState } from "react";
import { ListChecks, MoveRight, ShieldAlert } from "lucide-react";
import type { Change, Project, RationalTime } from "./types";
import { seconds } from "./types";
import type { ProductionClock } from "./creativeProduction";
import {
  inspectMusicConform,
  type MusicConformReview,
  type MusicConformTarget,
} from "./musicClock";

type MusicClock = Extract<ProductionClock,{kind:"music"}>;
type Props = {
  project:Project;
  savedClock:MusicClock|null;
  draftMatchesSaved:boolean;
  baseRevision:number;
  busy:boolean;
  commit:(change:Change)=>Promise<void>;
  onSeek:(seconds:number)=>void;
};
const fmt=(t:RationalTime):string => Number.isFinite(seconds(t))
  ?seconds(t).toFixed(3)+"s":"invalid";

function firstTargets(project:Project,clock:MusicClock|null):MusicConformTarget[] {
  if(!clock || project.scenes.length<2 || clock.beats.length<3) return [];
  return project.scenes.slice(0,2).map((scene,i)=>({
    scene_id:scene.id,
    beat_index:Math.min(i+1,clock.beats.length-1),
  }));
}

/** This controls one *new* Native SDK CAS, not two sequential edits. */
export default function MusicConformWorkbench({
  project,savedClock,draftMatchesSaved,baseRevision,busy,commit,onSeek,
}:Props) {
  const [targets,setTargets]=useState<MusicConformTarget[]>(()=>
    firstTargets(project,savedClock));
  const [ack,setAck]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [note,setNote]=useState<string|null>(null);
  const [running,setRunning]=useState(false);
  const source=project.assets.find(asset=>asset.id===savedClock?.asset_id &&
    asset.media_type.startsWith("audio/"));
  const digest=source?.content_sha256??"";
  const ready=!!savedClock && draftMatchesSaved && baseRevision===project.revision;
  const ordered=useMemo(()=>targets.slice().sort(
    (left,right)=>project.scenes.findIndex(s=>s.id===left.scene_id)-
      project.scenes.findIndex(s=>s.id===right.scene_id)
  ),[project.scenes,targets]);
  const result=useMemo(()=>{
    if(!ready||!digest)return {review:null as MusicConformReview|null,
      reason:"Save this exact source-bound music clock before conforming a whole sequence."};
    try {return {review:inspectMusicConform(project,ordered,digest),reason:null};}
    catch(e){return {review:null,reason:e instanceof Error?e.message:"The music conform could not be reviewed."};}
  },[project,ordered,digest,ready]);
  const review=result.review;
  const locked=busy||running||!ready;
  const resetApproval=()=>{setAck(false);setError(null);setNote(null);};
  useEffect(()=>{setAck(false);setError(null);},[project.revision,project.generation]);
  useEffect(()=>{
    setTargets(firstTargets(project,savedClock));
    resetApproval();
  },[project.id,savedClock?.asset_id]);
  const toggle=(sceneId:string,on:boolean,defaultIndex:number)=>{
    setTargets(current=>on?
      current.some(item=>item.scene_id===sceneId)?current:
      [...current,{scene_id:sceneId,
        beat_index:Math.min(defaultIndex+1,Math.max(0,(savedClock?.beats.length??1)-1))}]:
      current.filter(item=>item.scene_id!==sceneId));
    resetApproval();
  };
  const changeBeat=(sceneId:string,beatIndex:number)=>{
    setTargets(current=>current.map(item=>item.scene_id===sceneId?
      {...item,beat_index:beatIndex}:item));
    resetApproval();
  };
  const apply=async()=>{
    if(!review||locked||(review.anchors_need_acknowledgement&&!ack))return;
    setError(null);setNote(null);setRunning(true);
    try{
      await commit({
        type:"conform_scenes_to_music",
        targets:ordered,
        source_sha256:digest,
        base_revision:project.revision,
        acknowledge_absolute_anchors:ack,
      });
      setNote("One atomic musical conform committed. Affected scenes returned to Draft. Global cues and transcript timestamps stayed fixed; inspect each cut and final master before approving.");
      setAck(false);
    }catch(e){setError(e instanceof Error?e.message:"Musical conform was not applied.");}
    finally{setRunning(false);}
  };
  return <section className="music-conform" aria-label="Multi-scene musical conform">
    <header className="music-clock-heading"><ListChecks size={17} aria-hidden="true"/><div>
      <h3>Conform multiple cuts to the music grid</h3>
      <p>One source-locked project revision · explicit beat boundaries · no automatic audio retiming</p>
    </div></header>
    <p className="production-help">
      Select at least two scene endings and their exact saved beat times.
      Scene durations and later starts update together, or none of them change.
      This is an editorial timeline operation: beats are author-entered,
      not detected from the waveform.
    </p>
    <div className="music-conform-list" role="group" aria-label="Scene beat boundary assignments">
      {project.scenes.slice(0,16).map((scene,i)=>{
        const selected=targets.find(item=>item.scene_id===scene.id);
        return <div className="music-conform-row" key={scene.id}>
          <label>
            <input type="checkbox" checked={!!selected} disabled={locked}
              aria-label={"Conform "+scene.name}
              onChange={e=>toggle(scene.id,e.target.checked,i)}/>
            <span>{String(i+1).padStart(2,"0")} · {scene.name}</span>
          </label>
          <span className="mono">{fmt({
            num:String(scene.start.num),den:String(scene.start.den)
          })} · {fmt(scene.duration)} duration</span>
          <select aria-label={"Beat for "+scene.name} disabled={locked||!selected}
            value={selected?.beat_index??0}
            onChange={e=>changeBeat(scene.id,Number(e.target.value))}>
            {(savedClock?.beats??[]).slice(0,512).map((beat,index)=>
              <option key={index} value={index}>
                Beat {String(index+1).padStart(3,"0")} · {fmt(beat)}
              </option>)}
          </select>
        </div>;
      })}
    </div>
    <p className="production-help">
      Selected boundaries: {ordered.length} · max 16 per transaction.
      This operation preserves unselected scene durations; the
      original relative scene order is never changed.
    </p>
    {review?<div className="music-conform-review" aria-label="Multi-scene conform impact preview">
      <div className="music-cut-impact">
        <div><span>Changed source scenes</span><strong>{review.changed_scene_count}</strong></div>
        <div><span>Total film duration</span><strong>{fmt(review.original_duration)} → {fmt(review.conformed_duration)}</strong></div>
        <div><span>Fixed cues / markers / transcripts</span><strong>{review.affected_cue_count} / {review.affected_marker_count} / {review.affected_transcript_count}</strong></div>
        <div><span>Scene-linked SFX to review</span><strong>{review.affected_sfx_cue_count}</strong></div>
        <div><span>Source revision and identity</span><strong>r{review.base_revision} · {review.source_sha256.slice(0,12)}…</strong></div>
      </div>
      <div className="music-conform-changes">
        {review.scenes.filter(scene=>scene.changed).slice(0,12).map(item=>
          <div key={item.scene_id}>
            <strong>{project.scenes.find(scene=>scene.id===item.scene_id)?.name??"Unknown scene"}</strong>
            <span>{fmt(item.old_start)} → {fmt(item.new_start)} start</span>
            <span>{fmt(item.old_duration)} → {fmt(item.new_duration)} duration</span>
            {item.beat_index!==null?<span>Saved beat #{item.beat_index+1}</span>:
              <span>Shifted by an upstream selected boundary</span>}
          </div>)}
        {review.changed_scene_count>12&&<p>
          {review.changed_scene_count-12} additional affected scenes require inspection.
        </p>}
      </div>
    </div>:<p className="production-help" role="status">{result.reason}</p>}
    {review&&<div className="music-cut-footnote"><ShieldAlert size={17} aria-hidden="true"/>
      <p>Every affected scene returns to Draft and the previous concept approval is invalidated.
        Global audio cues, markers and transcripts retain their absolute times.
        Scene-linked SFX follow their scenes, with tails prevalidated; their
        new energy and sync still require independent listening and native playback.
        The entire change is rejected if a selected beat is invalid or any affected
        scene/profile is timing-locked.</p>
    </div>}
    {review?.anchors_need_acknowledgement&&<label className="production-check music-cut-acknowledge">
      <input type="checkbox" aria-label="Acknowledge fixed anchors across conformed scenes"
        checked={ack} disabled={locked} onChange={e=>setAck(e.target.checked)}/>
      I understand that {review.affected_cue_count+review.affected_marker_count+review.affected_transcript_count}
      fixed-time entries and {review.affected_sfx_cue_count} scene-linked SFX must be reviewed after this conform.
    </label>}
    <div className="production-button-row">
      <button className="primary-button" type="button"
        disabled={locked||!review||(review.anchors_need_acknowledgement&&!ack)}
        onClick={()=>void apply()}>
        {running?"Applying atomic conform…":"Apply multi-scene music conform"}
      </button>
      <button className="secondary-button" type="button" disabled={!review}
        onClick={()=>{const change=review?.scenes.find(item=>item.changed);
          if(change)onSeek(seconds(change.new_start));}}>
        <MoveRight size={14} aria-hidden="true"/> Review first changed scene
      </button>
    </div>
    {error&&<p className="production-error" role="alert">{error}</p>}
    {note&&<p className="production-help" role="status">{note}</p>}
  </section>;
}
