import type {AudioCue,Project,RationalTime,TranscriptSegment} from './types';
import type {NarrationReplacementResponse,NarrationSourceResponse} from './narrationReviewTypes';
import {validateNarrationImpactReply,validateNarrationSourceReply} from './narrationReviewApi';

export interface NarrationReviewOrigin {project:Project;reply:NarrationSourceResponse}
export interface NarrationRevisionRow<T> {id:string;before:T|null;after:T|null;status:'added'|'removed'|'changed'|'source_changed'}
export interface NarrationRevisionReview {
 origin:NarrationReviewOrigin;project:Project;reply:NarrationReplacementResponse;
 segments:NarrationRevisionRow<TranscriptSegment>[];cues:NarrationRevisionRow<AudioCue>[];
}
export interface NarrationCorrectionTarget {
 kind:'transcript'|'cue';id:string;project:Project;
}
// Equality is for stale UI observation, never a native digest or write permit.
const canonical=(value:unknown):string=>{
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
 if(value&&typeof value==='object'){
  const row=value as Record<string,unknown>;
  return '{'+Object.keys(row).sort().map(key=>JSON.stringify(key)+':'+canonical(row[key])).join(',')+'}';
 }
 return JSON.stringify(value)??'undefined';
};
export const sameNarrationReviewProject=(left:Project,right:Project)=>canonical(left)===canonical(right);
export function captureNarrationReviewOrigin(project:Project,reply:NarrationSourceResponse):NarrationReviewOrigin{
 validateNarrationSourceReply(project,reply);
 return {project:structuredClone(project),reply:structuredClone(reply)};
}
function rows<T extends {id:string}>(ids:string[],before:T[],after:T[]):NarrationRevisionRow<T>[] {
 if(new Set(ids).size!==ids.length)throw new Error('Narration impact repeats a source identity. Read it again.');
 const old=new Map(before.map(value=>[value.id,value])),current=new Map(after.map(value=>[value.id,value]));
 if(old.size!==before.length||current.size!==after.length)throw new Error('Narration source identities are ambiguous.');
 return ids.map(id=>{
  const a=old.get(id)??null,b=current.get(id)??null;
  if(!a&&!b)throw new Error('Narration impact references an unavailable original ID: '+id);
  const status:NarrationRevisionRow<T>['status']=!a?'added':!b?'removed':canonical(a)===canonical(b)?'source_changed':'changed';
  return {id,before:structuredClone(a),after:structuredClone(b),status};
 });
}
export function createNarrationRevisionReview(origin:NarrationReviewOrigin,project:Project,reply:NarrationReplacementResponse):NarrationRevisionReview{
 validateNarrationSourceReply(origin.project,origin.reply);
 if(origin.project.id!==project.id||origin.project.generation!==project.generation||origin.project.active_branch!==project.active_branch)
  throw new Error('Narration review belongs to another project, generation or branch. Select its source again.');
 validateNarrationImpactReply(project,origin.reply.source,reply);
 const active=project.audio.voice_tracks.find(track=>track.id===project.audio.active_voice_track_id);
 if(!active)throw new Error('The current narration source is unavailable.');
 const transcript=project.audio.transcript.filter(segment=>segment.voice_track_id===active.id);
 const ids=new Set(transcript.map(segment=>segment.id));
 const cues=project.audio.cues.filter(cue=>cue.source_segment_id!==null&&ids.has(cue.source_segment_id));
 return {origin:structuredClone(origin),project:structuredClone(project),reply:structuredClone(reply),
  segments:rows(reply.impact.changed_segment_ids,origin.reply.source.transcript,transcript),
  cues:rows(reply.impact.changed_cue_ids,origin.reply.source.cues,cues)};
}
export const narrationRevisionReviewCurrent=(review:NarrationRevisionReview|null,project:Project):review is NarrationRevisionReview=>
 !!review&&sameNarrationReviewProject(review.project,project);
export const exactNarrationTime=(value:RationalTime)=>value.num+'/'+value.den+' s';
/** Read-only navigation to an existing current field. Never activate or recreate a take. */
export function narrationCorrectionFocus(project:Project,target:NarrationCorrectionTarget):{fieldId:string;seconds:number}{
 if(!sameNarrationReviewProject(project,target.project))throw new Error('Narration impact is stale. Compare the current saved revision before opening a correction.');
 let time:RationalTime,fieldId:string;
 if(target.kind==='transcript'){
  const segment=project.audio.transcript.find(value=>value.id===target.id);
  if(!segment||segment.voice_track_id!==project.audio.active_voice_track_id)
   throw new Error('This original segment is not in the active take. It was not recreated or activated.');
  fieldId='audio-transcript-'+segment.id+'-text';time=segment.start;
 }else{
  const cue=project.audio.cues.find(value=>value.id===target.id);
  const segment=project.audio.transcript.find(value=>value.id===cue?.source_segment_id);
  if(!cue||!segment||segment.voice_track_id!==project.audio.active_voice_track_id)
   throw new Error('This original cue has no active narration source. No take was changed.');
  fieldId='audio-cue-'+cue.id+'-label';time=cue.at;
 }
 const seconds=Number(time.num)/Number(time.den);
 if(!Number.isFinite(seconds)||seconds<0)throw new Error('The source time cannot be displayed on the current playhead.');
 return {fieldId,seconds};
}
