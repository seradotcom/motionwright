import type {AudioCue,RationalTime,TranscriptSegment,VoiceTrack} from './types';
export interface NarrationSourceSnapshot {
 schema:'motionwright.narration-source-snapshot/1';
 project_id:string;generation:string;revision:number;
 voice_track:VoiceTrack;transcript:TranscriptSegment[];cues:AudioCue[];
 exact_content_sha256:string;source_classification:string;timing_review:
   'REQUIRES_MANUAL_TIMING_REVIEW'|'SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY';
 human_owner_lock_authenticated:false;audio_media_bytes_decoded:false;
 publication_approved:false;
}
export interface NarrationSourceResponse {
 schema:'motionwright.narration-source-preview/1';
 source:NarrationSourceSnapshot;project_revision:number;
 read_only:true;lock_authenticated:false;media_decoded:false;
 creative_approval:'REQUIRES_HUMAN_REVIEW';
}
export interface NarrationAffectedSpan {
 source_segment_id:string;start:RationalTime;end:RationalTime;
}
export interface NarrationReplacementImpact {
 schema:'motionwright.narration-impact-preview/1';
 previous_project_revision:number;next_project_revision:number;
 previous_source_sha256:string;candidate_source_sha256:string;
 changed_segment_ids:string[];changed_cue_ids:string[];
 affected_spans:NarrationAffectedSpan[];voice_media_replaced:boolean;
 caption_timing_invalidation:string;cuts_broll_invalidation:string;
 sound_mix_invalidation:string;scene_dependency_validation:'NOT_RUN';
 origin_locked_source_unchanged:true;candidate_committed:false;
 human_owner_approved:false;
}
export interface NarrationReplacementResponse {
 schema:'motionwright.narration-impact-preview/1';
 impact:NarrationReplacementImpact;
 applied:false;source_locked:false;owner_approval:'REQUIRED';
 media_or_captions_rendered:false;
}
