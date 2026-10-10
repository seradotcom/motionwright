import {useEffect,useState} from 'react';
import {BookOpenCheck,GitCompareArrows,LockKeyhole} from 'lucide-react';
import type {Project} from './types';
import type {NarrationSourceSnapshot,NarrationReplacementImpact} from './narrationReviewTypes';
import {nativeNarrationReplacementImpact,nativeNarrationSourceSnapshot} from './api';

const reason=(value:unknown)=>value instanceof Error?value.message:String(value);
const seconds=(value:{num:number;den:number})=>(value.num/value.den).toFixed(3)+' s';

export default function NarrationTakeReview({
  project,desktopMode
}:{
  project:Project;desktopMode:boolean
}){
  const [baseline,setBaseline]=useState<NarrationSourceSnapshot|null>(null);
  const [impact,setImpact]=useState<NarrationReplacementImpact|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  // A cross-project source must never migrate into another owner's take review.
  useEffect(()=>{setBaseline(null);setImpact(null);setError(null);},
    [project.id,project.generation]);
  useEffect(()=>{setImpact(null);setError(null);},[project.revision]);
  const capture=async()=>{
    setBusy(true);setError(null);setImpact(null);
    try{
      const result=await nativeNarrationSourceSnapshot(project);
      if(result.source.project_id!==project.id||
         result.source.generation!==project.generation||
         result.project_revision!==project.revision||
         result.source.human_owner_lock_authenticated){
        throw new Error('Narration source snapshot did not match the selected editorial revision.');
      }
      setBaseline(result.source);
    }catch(failure){setError(reason(failure));}finally{setBusy(false);}
  };
  const compare=async()=>{
    if(!baseline)return;
    setBusy(true);setError(null);setImpact(null);
    try{
      const next=await nativeNarrationReplacementImpact(project,baseline);
      if(next.impact.previous_source_sha256!==baseline.exact_content_sha256||
         next.impact.next_project_revision!==project.revision||
         next.applied||next.source_locked||next.media_or_captions_rendered){
        throw new Error('Narration impact preview attempted to claim unexpected edit or approval authority.');
      }
      setImpact(next.impact);
    }catch(failure){setError(reason(failure));}finally{setBusy(false);}
  };
  return <details className="audio-narration-source-review">
    <summary><LockKeyhole size={15}/> Narration take · source and edit-dependency review</summary>
    <p className="production-help">Review the measured voice, transcript and cues of the current project. This only records an in-memory comparison baseline — not an authenticated approval, an audio lock, or an exported/decoded master.</p>
    {!desktopMode&&<p className="production-help">The browser demo cannot invent a measured or approved source take.</p>}
    <div className="audio-row-actions">
      <button type="button" className="button button-secondary"
        disabled={!desktopMode||busy||!project.audio.active_voice_track_id}
        onClick={()=>void capture()}><BookOpenCheck size={14}/>
        {baseline?'Select current source as new baseline':'Review measured source baseline'}</button>
      <button type="button" className="button button-secondary"
        disabled={!desktopMode||busy||!baseline||
          baseline.project_id!==project.id||
          baseline.generation!==project.generation||
          baseline.revision>=project.revision}
        onClick={()=>void compare()}><GitCompareArrows size={14}/> Compare newer revision</button>
    </div>
    {error&&<p className="portable-message error" role="alert">{error}</p>}
    {baseline&&<section className="audio-narration-source-evidence" aria-label="Original narration source snapshot">
      <p><strong>Original source revision {baseline.revision}</strong> · {baseline.voice_track.label}</p>
      <p className="mono">Source {baseline.exact_content_sha256.slice(0,16)}… · imported audio {baseline.voice_track.source_sha256.slice(0,16)}…</p>
      <p>{baseline.transcript.length} measured/manual segments · {baseline.cues.length} related cues · {baseline.timing_review.replaceAll('_',' ').toLowerCase()}</p>
      <p>Owner lock: not authenticated · imported audio decode in this comparison: not performed</p>
    </section>}
    {impact&&<section className="audio-narration-source-impact" aria-label="Narration edit impact">
      <p><strong>Source impact: revision {impact.previous_project_revision} → {impact.next_project_revision}</strong></p>
      <p>{impact.changed_segment_ids.length} changed transcript segments · {impact.changed_cue_ids.length} affected cues · original voice file {impact.voice_media_replaced?'replaced':'unchanged'}</p>
      <ul>{impact.affected_spans.map((span,index)=><li key={span.source_segment_id+'-'+index}>
        {span.source_segment_id.slice(0,8)} · {seconds(span.start)} → {seconds(span.end)}
      </li>)}</ul>
      <p>{impact.caption_timing_invalidation.replaceAll('_',' ')}. {impact.cuts_broll_invalidation.replaceAll('_',' ')}.</p>
      <p>{impact.sound_mix_invalidation.replaceAll('_',' ')}. Scene dependency validation: not run.</p>
      <p>No replacement was committed here. Original words and cues remain available for human comparison; publication and approval are still separate decisions.</p>
    </section>}
  </details>;
}
