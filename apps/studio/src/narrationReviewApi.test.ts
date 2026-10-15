import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import type {NarrationSourceResponse} from "./narrationReviewTypes";
import {validateNarrationSourceReply,validateNarrationImpactReply} from "./narrationReviewApi";

function source(){
 const p=structuredClone(fixtureProject);
 const id="00000000-0000-4000-8000-000000000041";
 const asset={...p.assets[0],id,media_type:"audio/wav",content_sha256:"a".repeat(64)};
 p.assets.push(asset);p.production_design=emptyProductionDesign();
 p.audio.voice_tracks=[{id,asset_id:id,label:"Original measured take",sample_rate_hz:48000,
   channels:1,measured_duration:{num:"3",den:"1"},source_sha256:"a".repeat(64),
   loudness_lufs:null,true_peak_dbfs:null}];
 p.audio.active_voice_track_id=id;
 p.audio.transcript=[{id,voice_track_id:id,start:{num:"0",den:"1"},end:{num:"1",den:"1"},
   speaker:null,text:"Original spoken words",alignment:{kind:"manual" as const}}];
 p.audio.cues=[];
 const reply:NarrationSourceResponse={
   schema:"motionwright.narration-source-preview/1",project_revision:p.revision,
   source:{schema:"motionwright.narration-source-snapshot/1",
     project_id:p.id,generation:p.generation,revision:p.revision,
     voice_track:structuredClone(p.audio.voice_tracks[0]),
     transcript:structuredClone(p.audio.transcript),cues:[],
     exact_content_sha256:"b".repeat(64),
     source_classification:"EXISTING_PROJECT_AUDIO_NOT_IMPORTED_ASR_TRUTH",
     timing_review:"SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY",
     human_owner_lock_authenticated:false,audio_media_bytes_decoded:false,publication_approved:false},
   recordable_content_sha256:"c".repeat(64),recordable_source_verified:true,
   recorded_take:null,read_only:true,lock_authenticated:false,media_decoded:false,
   creative_approval:"REQUIRES_HUMAN_REVIEW",
 };
 return {p,reply};
}
describe("narration source receipts refuse forged client authority",()=>{
 it("compares the exact original measured take against canonical project state",()=>{
   const {p,reply}=source();
   expect(validateNarrationSourceReply(p,reply)).toBeDefined();
   expect(()=>validateNarrationSourceReply(p,{...reply,lock_authenticated:true})).toThrow();
   expect(()=>validateNarrationSourceReply(p,{...reply,signed:true})).toThrow();
   const altered=structuredClone(reply);altered.source.transcript[0].text="Other words";
   expect(()=>validateNarrationSourceReply(p,altered)).toThrow();
   const missing=structuredClone(p);missing.assets=missing.assets.filter(a=>a.id!==p.audio.active_voice_track_id);
   expect(()=>validateNarrationSourceReply(missing,reply)).toThrow();
 });
 it("rejects missing whole-project cut coverage before treating a proposal as reviewable",()=>{
   const {p,reply}=source();p.revision++;
   const noScenes={schema:"motionwright.narration-impact-preview/1",
    impact:{schema:"motionwright.narration-impact-preview/1",previous_project_revision:reply.source.revision,
      next_project_revision:p.revision,previous_source_sha256:reply.source.exact_content_sha256,
      candidate_source_sha256:"d".repeat(64),changed_segment_ids:[],changed_cue_ids:[],
      affected_spans:[],voice_media_replaced:false,
      caption_timing_invalidation:"CAPTIONS_MUST_REGENERATE_AND_HUMAN_REVIEW",
      cuts_broll_invalidation:"CUTS_AND_BROLL_REQUIRE_REEVALUATION",
      sound_mix_invalidation:"MIX_MUST_REVIEW_CUE_AND_TIMING_CONSEQUENCES",
      scene_dependency_validation:"NOT_RUN",origin_locked_source_unchanged:true,
      candidate_committed:false,human_owner_approved:false},
    timeline_review:{schema:"motionwright.narration-timeline-dependency-preview/1",
      project_id:p.id,generation:p.generation,source_revision:reply.source.revision,
      current_revision:p.revision,exact_source_sha256:"d".repeat(64),
      locally_overlapping_scene_ids:[],scene_ids_requiring_cut_review:[],
      scene_ids_requiring_broll_review:[],
      deliverable_ids_requiring_caption_review:[],source_scene_count:p.scenes.length,
      dependency_scope:"CONSERVATIVE_WHOLE_PROJECT_CUT_BROLL_REVIEW",impact_reason:"review",
      original_content_preserved:true,project_mutated:false,media_rendered:false,
      audio_regenerated:false,human_approved:false},
    applied:false,source_locked:false,owner_approval:"REQUIRED",
    media_or_captions_rendered:false};
   expect(()=>validateNarrationImpactReply(p,reply.source,noScenes)).toThrow();
   expect(()=>validateNarrationImpactReply(p,reply.source,{...noScenes,source_locked:true})).toThrow();
 });
});
