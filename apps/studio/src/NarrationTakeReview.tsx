import {useEffect,useState} from 'react';
import {BookOpenCheck,GitCompareArrows,LockKeyhole} from 'lucide-react';
import type {Project,RationalTime,Change} from './types';
import type {NarrationSourceSnapshot,NarrationReplacementImpact,NarrationTimelineReview} from './narrationReviewTypes';
import {nativeNarrationReplacementImpact,nativeNarrationSourceSnapshot} from './api';

const reason=(value:unknown)=>value instanceof Error?value.message:String(value);
// Display approximation only. The source and impact retain exact rational
// num/den *strings* from the canonical domain without any re-quantization.
const seconds=(value:RationalTime)=>{
  const numerator=Number(value.num),denominator=Number(value.den);
  const estimate=numerator/denominator;
  return Number.isFinite(estimate)&&denominator!==0
    ? estimate.toFixed(3)+' s ≈' : 'rational timing unavailable';
};

export default function NarrationTakeReview({
  project,desktopMode,commit
}:{
  project:Project;desktopMode:boolean;commit:(change:Change)=>Promise<void>
}){
  const [baseline,setBaseline]=useState<NarrationSourceSnapshot|null>(null);
  const [impact,setImpact]=useState<NarrationReplacementImpact|null>(null);
  const [timelineReview,setTimelineReview]=useState<NarrationTimelineReview|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null);
  const [recordableSha,setRecordableSha]=useState<string|null>(null);
  const [recordedReviewer,setRecordedReviewer]=useState('');
  const [recordedReason,setRecordedReason]=useState('');
  const [confirmed,setConfirmed]=useState(false);
  const locked=project.production_design?.narration_take_lock??null;
  // A cross-project source must never migrate into another owner's take review.
  useEffect(()=>{
    setBaseline(null);setImpact(null);setTimelineReview(null);setError(null);setRecordableSha(null);
    setRecordedReviewer('');setRecordedReason('');setConfirmed(false);
  },[project.id,project.generation]);
  useEffect(()=>{setImpact(null);setTimelineReview(null);setError(null);setRecordableSha(null);setConfirmed(false);},[project.revision]);
  const capture=async()=>{
    setBusy(true);setError(null);setImpact(null);setTimelineReview(null);
    try{
      const result=await nativeNarrationSourceSnapshot(project);
      if(result.source.project_id!==project.id||
         result.source.generation!==project.generation||
         result.project_revision!==project.revision||
         result.source.human_owner_lock_authenticated){
        throw new Error('Narration source snapshot did not match the selected editorial revision.');
      }
      setBaseline(result.source);
      setRecordableSha(result.recordable_source_verified?result.recordable_content_sha256:null);
      if(result.recorded_take?.source_content_sha256 && locked
        && result.recorded_take.source_content_sha256!==locked.source_content_sha256)
        throw new Error('Readback of persisted narration approval differs from this project snapshot.');
    }catch(failure){setError(reason(failure));}finally{setBusy(false);}
  };
  const compare=async()=>{
    if(!baseline)return;
    setBusy(true);setError(null);setImpact(null);setTimelineReview(null);
    try{
      const next=await nativeNarrationReplacementImpact(project,baseline);
      if(next.impact.previous_source_sha256!==baseline.exact_content_sha256||
         next.impact.next_project_revision!==project.revision||
         next.applied||next.source_locked||next.media_or_captions_rendered){
        throw new Error('Narration impact preview attempted to claim unexpected edit or approval authority.');
      }
      if(next.timeline_review.project_id!==project.id||
         next.timeline_review.generation!==project.generation||
         next.timeline_review.current_revision!==project.revision||
         next.timeline_review.exact_source_sha256!==next.impact.candidate_source_sha256||
         next.timeline_review.project_mutated||next.timeline_review.media_rendered||
         next.timeline_review.human_approved){
        throw new Error('Narration placement review belongs to another source or falsely claims execution/approval.');
      }
      setTimelineReview(next.timeline_review);
      setImpact(next.impact);
    }catch(failure){setError(reason(failure));}finally{setBusy(false);}
  };
  const commitNarration=async(kind:'record'|'release')=>{
    setBusy(true);setError(null);
    try{
      if(!desktopMode||!confirmed||recordedReviewer.trim().length<2||!recordedReason.trim())
        throw new Error('An explicit local editorial review, reviewer label and reason are required.');
      if(kind==='record'){
        if(locked||!baseline||!recordableSha||baseline.revision!==project.revision)
          throw new Error('Review the current eligible measured narration source before recording the decision.');
        await commit({type:'record_narration_take',expected_source_sha256:recordableSha,
          expected_project_revision:project.revision,reviewer:recordedReviewer.trim(),
          reason:recordedReason.trim()});
      }else{
        if(!locked)throw new Error('There is no recorded narration decision to release.');
        await commit({type:'release_narration_take',
          expected_source_sha256:locked.source_content_sha256,
          reviewer:recordedReviewer.trim(),reason:recordedReason.trim()});
      }
      setRecordableSha(null);setBaseline(null);setConfirmed(false);setRecordedReason('');
    }catch(failure){setError(reason(failure));}
    finally{setBusy(false);}
  };
  return <details className="audio-narration-source-review">
    <summary><LockKeyhole size={15}/> Narration take · source and edit-dependency review</summary>
    <p className="production-help">Inspect the measured voice, transcript and cues of the current project. A baseline is only an in-memory comparison; recording source protection requires a separate user-confirmed revision below. Neither action authenticates identity, decodes a master or grants publication rights.</p>
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
    {locked&&<section className="audio-narration-source-evidence" aria-label="Recorded and protected narration source">
      <p><strong>Recorded source content protection</strong> · approved from revision {locked.source_project_revision}</p>
      <p>Reviewer label: {locked.recorded_reviewer} · {locked.recorded_reason}</p>
      <p className="mono">Locked source {locked.source_content_sha256.slice(0,16)}…</p>
      <p>Voice, transcript and source-linked cues are protected from edits until a separate explicit release. The mix remains editable. Reviewer identity is not independently authenticated; media quality and publication remain unapproved.</p>
    </section>}
    {desktopMode&&(baseline||locked)&&<section className="audio-narration-source-evidence" aria-label="Record or release source content decision">
      <p><strong>{locked?'Release recorded source for new editorial changes':'Record current original narration source'}</strong></p>
      {!locked&&!recordableSha&&<p className="production-help">Cannot record: original voice asset, manual/high-confidence timing or source cues lack complete admissible evidence. Correct the native source, then review again.</p>}
      <label className="production-field"><span>Reviewer label (recorded, not identity proof)</span>
        <input value={recordedReviewer} maxLength={200} disabled={busy}
          onChange={event=>{setRecordedReviewer(event.target.value);setConfirmed(false);}}/>
      </label>
      <label className="production-field"><span>{locked?'Why release this source?':'Why preserve these exact voice words and timing?'}</span>
        <textarea value={recordedReason} rows={2} maxLength={2000} disabled={busy}
          onChange={event=>{setRecordedReason(event.target.value);setConfirmed(false);}}/>
      </label>
      <label className="production-check">
        <input type="checkbox" checked={confirmed} disabled={busy}
          onChange={event=>setConfirmed(event.target.checked)}/>
        I am recording a deliberate editorial source decision. This is not an authenticated signature or permission to publish.
      </label>
      <button type="button" className="button button-secondary"
        disabled={busy||!confirmed||!recordedReviewer.trim()||!recordedReason.trim()||
          (!locked&&(!recordableSha||!baseline||baseline.revision!==project.revision))}
        onClick={()=>void commitNarration(locked?'release':'record')}>
        <LockKeyhole size={14}/> {locked?'Release exact recorded source':'Record and protect this source revision'}
      </button>
    </section>}
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
      {timelineReview&&<div className="audio-narration-source-evidence" aria-label="Narration source dependency map">
        <strong>Source-bound dependency review · no edits applied</strong>
        <p>Local transcript overlaps: {timelineReview.locally_overlapping_scene_ids.length?
            timelineReview.locally_overlapping_scene_ids.map(id=>
              project.scenes.find(scene=>scene.id===id)?.name??id.slice(0,8)).join(', '):
            'none demonstrated'}</p>
        <p>All {timelineReview.scene_ids_requiring_cut_review.length} scenes require a cut review; all {timelineReview.scene_ids_requiring_broll_review.length} require a B-roll continuity review because indirect temporal effects remain unknown.</p>
        <p>{timelineReview.deliverable_ids_requiring_caption_review.length} caption-enabled delivery profiles require fresh review.</p>
        <p>Changes to transitions, captions, B-roll, audio and encoder output have not been executed. Review the full project before publication.</p>
      </div>}
      <p>No replacement was committed here. Original words and cues remain available for human comparison; publication and approval are still separate decisions.</p>
    </section>}
  </details>;
}
