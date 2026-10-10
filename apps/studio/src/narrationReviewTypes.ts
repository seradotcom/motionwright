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
 recordable_content_sha256:string|null;recordable_source_verified:boolean;
 recorded_take:import('./creativeProduction').NarrationTakeLock|null;
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
export interface NarrationTimelineReview {
 schema:'motionwright.narration-timeline-dependency-preview/1';
 project_id:string;generation:string;source_revision:number;current_revision:number;
 exact_source_sha256:string;locally_overlapping_scene_ids:string[];
 scene_ids_requiring_cut_review:string[];scene_ids_requiring_broll_review:string[];
 deliverable_ids_requiring_caption_review:string[];source_scene_count:number;
 dependency_scope:'CONSERVATIVE_WHOLE_PROJECT_CUT_BROLL_REVIEW';
 impact_reason:string;original_content_preserved:true;project_mutated:false;
 media_rendered:false;audio_regenerated:false;human_approved:false;
}
export interface NarrationReplacementResponse {
 schema:'motionwright.narration-impact-preview/1';
 impact:NarrationReplacementImpact;
 timeline_review:NarrationTimelineReview;
 applied:false;source_locked:false;owner_approval:'REQUIRED';
 media_or_captions_rendered:false;
}
