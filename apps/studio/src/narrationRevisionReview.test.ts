import {describe,expect,it} from 'vitest';
import {fixtureProject} from './fixture';
import {emptyProductionDesign} from './creativeProduction';
import type {NarrationReplacementResponse,NarrationSourceResponse} from './narrationReviewTypes';
import {captureNarrationReviewOrigin,createNarrationRevisionReview,exactNarrationTime,
 narrationCorrectionFocus,narrationRevisionReviewCurrent} from './narrationRevisionReview';

const id=(n:number)=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
function fixture(){
 const project=structuredClone(fixtureProject);project.schema_version=2;project.locks=[];
 project.production_design=emptyProductionDesign();
 project.assets.push({id:id(901),name:'Controlled original narration',media_type:'audio/wav',content_sha256:'a'.repeat(64),source_revision:null});
 project.audio.voice_tracks=[{id:id(902),asset_id:id(901),label:'Controlled source take',sample_rate_hz:48000,channels:1,
  measured_duration:{num:'5',den:'1'},source_sha256:'a'.repeat(64),loudness_lufs:null,true_peak_dbfs:null}];
 project.audio.active_voice_track_id=id(902);
 project.audio.transcript=[{id:id(903),voice_track_id:id(902),start:{num:'1001',den:'30000'},end:{num:'50051',den:'30000'},
  speaker:'  Narrator A  ',text:'  Original words\nwith retained whitespace  ',alignment:{kind:'manual'}}];
 project.audio.cues=[{id:id(904),at:{num:'1001',den:'30000'},label:'  Original cue  ',source_segment_id:id(903),evidence:'manual'}];
 const source:NarrationSourceResponse={schema:'motionwright.narration-source-preview/1',project_revision:project.revision,
  source:{schema:'motionwright.narration-source-snapshot/1',project_id:project.id,generation:project.generation,revision:project.revision,
   voice_track:structuredClone(project.audio.voice_tracks[0]),transcript:structuredClone(project.audio.transcript),cues:structuredClone(project.audio.cues),
   exact_content_sha256:'b'.repeat(64),source_classification:'EXISTING_PROJECT_AUDIO_NOT_IMPORTED_ASR_TRUTH',
   timing_review:'SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY',human_owner_lock_authenticated:false,audio_media_bytes_decoded:false,publication_approved:false},
  recordable_content_sha256:'c'.repeat(64),recordable_source_verified:true,recorded_take:null,read_only:true,lock_authenticated:false,media_decoded:false,creative_approval:'REQUIRES_HUMAN_REVIEW'};
 const next=structuredClone(project);next.revision++;next.audio.transcript[0].text='  Current words\nwith a deliberate correction  ';
 next.audio.transcript[0].end={num:'60061',den:'30000'};
 const reply:NarrationReplacementResponse={schema:'motionwright.narration-impact-preview/1',
  impact:{schema:'motionwright.narration-impact-preview/1',previous_project_revision:project.revision,next_project_revision:next.revision,
   previous_source_sha256:source.source.exact_content_sha256,candidate_source_sha256:'d'.repeat(64),changed_segment_ids:[id(903)],changed_cue_ids:[id(904)],
   affected_spans:[{source_segment_id:id(903),start:project.audio.transcript[0].start,end:next.audio.transcript[0].end}],voice_media_replaced:false,
   caption_timing_invalidation:'CAPTIONS_MUST_REGENERATE_AND_HUMAN_REVIEW',cuts_broll_invalidation:'CUTS_AND_BROLL_REQUIRE_REEVALUATION',
   sound_mix_invalidation:'MIX_MUST_REVIEW_CUE_AND_TIMING_CONSEQUENCES',scene_dependency_validation:'NOT_RUN',origin_locked_source_unchanged:true,candidate_committed:false,human_owner_approved:false},
  timeline_review:{schema:'motionwright.narration-timeline-dependency-preview/1',project_id:next.id,generation:next.generation,
   source_revision:project.revision,current_revision:next.revision,exact_source_sha256:'d'.repeat(64),locally_overlapping_scene_ids:[],
   scene_ids_requiring_cut_review:next.scenes.map(scene=>scene.id),scene_ids_requiring_broll_review:next.scenes.map(scene=>scene.id),
   deliverable_ids_requiring_caption_review:next.deliverables.filter(profile=>profile.captions||profile.burn_in_captions).map(profile=>profile.id),
   source_scene_count:next.scenes.length,dependency_scope:'CONSERVATIVE_WHOLE_PROJECT_CUT_BROLL_REVIEW',impact_reason:'controlled source test',
   original_content_preserved:true,project_mutated:false,media_rendered:false,audio_regenerated:false,human_approved:false},
  applied:false,source_locked:false,owner_approval:'REQUIRED',media_or_captions_rendered:false};
 return {project,source,next,reply};
}

// Projection/correction tests use explicit data receipts. They do not execute
// the native Rust boundary, decode a take or claim a persisted change.
describe('Narration source comparison and existing-field correction',()=>{
 it('shows exact old/new words, speaker, rational times and original IDs without changing either source',()=>{
  const value=fixture(),before=structuredClone(value),origin=captureNarrationReviewOrigin(value.project,value.source);
  const review=createNarrationRevisionReview(origin,value.next,value.reply);
  expect(review.segments[0]).toEqual({id:id(903),before:value.project.audio.transcript[0],after:value.next.audio.transcript[0],status:'changed'});
  expect(exactNarrationTime(review.segments[0].before!.start)).toBe('1001/30000 s');
  expect(exactNarrationTime(review.segments[0].after!.end)).toBe('60061/30000 s');
  expect(value).toEqual(before);expect(review.project).not.toBe(value.next);
 });
 it('keeps a native-linked unchanged cue visible without inventing a cue edit',()=>{
  const v=fixture(),review=createNarrationRevisionReview(captureNarrationReviewOrigin(v.project,v.source),v.next,v.reply);
  expect(review.cues[0].status).toBe('source_changed');expect(review.cues[0].before).toEqual(review.cues[0].after);
  expect(review.reply.applied).toBe(false);expect(review.reply.timeline_review.human_approved).toBe(false);
 });
 it('compares removed and added identities after a take switch without joining similar text or changing the active take',()=>{
  const v=fixture(),original=structuredClone(v.next.audio.transcript[0]);
  v.next.audio.voice_tracks.push({...v.next.audio.voice_tracks[0],id:id(905)});v.next.audio.active_voice_track_id=id(905);
  v.next.audio.transcript.push({...original,id:id(906),voice_track_id:id(905),text:v.source.source.transcript[0].text});
  v.reply.impact.voice_media_replaced=true;v.reply.impact.sound_mix_invalidation='MIX_VOICE_BUS_MUST_REMEASURE';v.reply.impact.changed_segment_ids=[id(903),id(906)];
  const before=structuredClone(v.next),review=createNarrationRevisionReview(captureNarrationReviewOrigin(v.project,v.source),v.next,v.reply);
  expect(review.segments.map(row=>[row.id,row.status])).toEqual([[id(903),'removed'],[id(906),'added']]);
  expect(review.segments[0].before!.text).toBe(review.segments[1].after!.text);
  expect(v.next).toEqual(before);expect(review.cues[0].status).toBe('removed');
 });
 it.each(['changed_segment_ids','changed_cue_ids'] as const)('refuses an unavailable original ID in %s',key=>{
  const v=fixture();v.reply.impact[key]=[id(999)];
  expect(()=>createNarrationRevisionReview(captureNarrationReviewOrigin(v.project,v.source),v.next,v.reply)).toThrow(/unavailable original ID/);
 });
 it('rejects duplicate impact identities instead of displaying a misleading repeated change',()=>{
  const v=fixture();v.reply.impact.changed_segment_ids.push(id(903));
  expect(()=>createNarrationRevisionReview(captureNarrationReviewOrigin(v.project,v.source),v.next,v.reply)).toThrow(/repeats a source identity/);
 });
 it('rejects a baseline whose words disagree with the original Project',()=>{
  const v=fixture();v.source.source.transcript[0].text='Forged earlier words';
  expect(()=>captureNarrationReviewOrigin(v.project,v.source)).toThrow(/baseline source/);
 });
 it.each(['id','generation','active_branch'] as const)('cannot reuse a source comparison across %s',field=>{
  const v=fixture(),origin=captureNarrationReviewOrigin(v.project,v.source);v.next[field]=id(999);
  expect(()=>createNarrationRevisionReview(origin,v.next,v.reply)).toThrow(/another project, generation or branch/);
 });
 it.each(['deliverables','audio','production_design'] as const)('marks same-revision %s differences stale and refuses navigation',field=>{
  const v=fixture(),review=createNarrationRevisionReview(captureNarrationReviewOrigin(v.project,v.source),v.next,v.reply),changed=structuredClone(v.next);
  if(field==='deliverables')changed.deliverables[0].width++;
  else if(field==='audio')changed.audio.mix.voice_gain_db++;
  else delete changed.production_design;
  expect(narrationRevisionReviewCurrent(review,changed)).toBe(false);
  expect(()=>narrationCorrectionFocus(changed,{kind:'transcript',id:id(903),project:review.project})).toThrow(/stale/);
 });
 it('finds the existing transcript and cue fields without writing, retiming or activating anything',()=>{
  const v=fixture(),before=structuredClone(v.next);
  expect(narrationCorrectionFocus(v.next,{kind:'transcript',id:id(903),project:v.next})).toEqual({fieldId:'audio-transcript-'+id(903)+'-text',seconds:1001/30000});
  expect(narrationCorrectionFocus(v.next,{kind:'cue',id:id(904),project:v.next})).toEqual({fieldId:'audio-cue-'+id(904)+'-label',seconds:1001/30000});
  expect(v.next).toEqual(before);
 });
 it.each(['removed','inactive'] as const)('does not recreate or activate a %s transcript target',kind=>{
  const v=fixture();if(kind==='removed')v.next.audio.transcript=[];else v.next.audio.active_voice_track_id=id(999);
  expect(()=>narrationCorrectionFocus(v.next,{kind:'transcript',id:id(903),project:v.next})).toThrow(/not in the active take/);
 });
 it('refuses an orphan cue target rather than inferring its narration source',()=>{
  const v=fixture();v.next.audio.cues[0].source_segment_id=null;
  expect(()=>narrationCorrectionFocus(v.next,{kind:'cue',id:id(904),project:v.next})).toThrow(/no active narration source/);
 });
 it('does not publish navigation for a nonfinite display time',()=>{
  const v=fixture();v.next.audio.transcript[0].start.den='0';
  expect(()=>narrationCorrectionFocus(v.next,{kind:'transcript',id:id(903),project:v.next})).toThrow(/cannot be displayed/);
 });
});
