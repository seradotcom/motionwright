/** Read-only source-bound Tauri transport for the original active narration.
 * Native Rust performs the full canonical digest and revision checks. */
import {invoke} from "@tauri-apps/api/core";
import type {Project} from "./types";
import type {
 NarrationSourceResponse,NarrationSourceSnapshot,NarrationReplacementResponse,
} from "./narrationReviewTypes";

const HEX=/^[0-9a-f]{64}$/;
const desktop=()=>typeof window!=="undefined"&&"__TAURI_INTERNALS__" in window;
const invalid=(reason:string):never=>{throw new Error(reason)};
const scope=(project:Project)=>({
 project_id:project.id,generation:project.generation,revision:project.revision,
});
const record=(raw:unknown,where:string):Record<string,unknown>=>{
 if(!raw||typeof raw!=="object"||Array.isArray(raw))
   return invalid(where+" must be a source-bound object.");
 return raw as Record<string,unknown>;
};
const keys=(row:Record<string,unknown>,set:string[],where:string)=>{
 if(Object.keys(row).length!==set.length||set.some(k=>!Object.hasOwn(row,k)))
   invalid(where+" contains unrecognized or missing fields.");
};
const canonical=(value:unknown):string=>{
 if(Array.isArray(value))return "["+value.map(canonical).join(",")+"]";
 if(value && typeof value==="object"){
   const row=value as Record<string,unknown>;
   return "{"+Object.keys(row).sort().map(key=>JSON.stringify(key)+":"+canonical(row[key])).join(",")+"}";
 }
 const literal=JSON.stringify(value);
 if(typeof literal!=="string")return invalid("Narration source contains an unserializable value.");
 return literal;
};
const noClaims=(row:Record<string,unknown>,list:string[],where:string)=>{
 for(const key of list)if(row[key]!==false)
   invalid(where+" must not claim human authentication, publication or native execution.");
};
export function validateNarrationSourceReply(project:Project,value:unknown):NarrationSourceResponse{
 const reply=record(value,"Narration source receipt");
 keys(reply,["schema","source","project_revision","recordable_content_sha256",
   "recordable_source_verified","recorded_take","read_only","lock_authenticated",
   "media_decoded","creative_approval"],"Narration source receipt");
 if(reply.schema!=="motionwright.narration-source-preview/1"||
    reply.project_revision!==project.revision||reply.read_only!==true||
    reply.creative_approval!=="REQUIRES_HUMAN_REVIEW")
   invalid("Original narration receipt belongs to another project revision.");
 noClaims(reply,["lock_authenticated","media_decoded"],"Narration source receipt");
 const src=record(reply.source,"Narration baseline");
 keys(src,["schema","project_id","generation","revision","voice_track",
   "transcript","cues","exact_content_sha256","source_classification",
   "timing_review","human_owner_lock_authenticated","audio_media_bytes_decoded",
   "publication_approved"],"Narration baseline");
 if(src.schema!=="motionwright.narration-source-snapshot/1"||
    src.project_id!==project.id||src.generation!==project.generation||
    src.revision!==project.revision||
    typeof src.exact_content_sha256!=="string"||!HEX.test(src.exact_content_sha256)||
    !["REQUIRES_MANUAL_TIMING_REVIEW","SOURCE_MANUAL_OR_MEASURED_HIGH_CONFIDENCE_ONLY"].includes(src.timing_review as string)||
    !Array.isArray(src.transcript)||!Array.isArray(src.cues)||
    src.transcript.length>5000||src.cues.length>5000)
   invalid("Narration baseline source, timing or digest is invalid.");
 noClaims(src,["human_owner_lock_authenticated","audio_media_bytes_decoded","publication_approved"],"Narration baseline");
 if(typeof reply.recordable_source_verified!=="boolean"||
   !(reply.recordable_content_sha256===null ||
     typeof reply.recordable_content_sha256==="string"&&HEX.test(reply.recordable_content_sha256))||
   (reply.recordable_source_verified!== (reply.recordable_content_sha256!==null)))
    invalid("Recordable narration source is inconsistent or unverifiable.");
 const recorded=project.production_design?.narration_take_lock??null;
 if(canonical(reply.recorded_take)!==canonical(recorded))
   invalid("Narration lock readback does not match current canonical project snapshot.");
 return reply as unknown as NarrationSourceResponse;
}
export function validateNarrationImpactReply(project:Project,original:NarrationSourceSnapshot,value:unknown):NarrationReplacementResponse{
 const reply=record(value,"Narration replacement response");
 keys(reply,["schema","impact","timeline_review","applied","source_locked",
   "owner_approval","media_or_captions_rendered"],"Narration replacement response");
 noClaims(reply,["applied","source_locked","media_or_captions_rendered"],"Narration replacement response");
 if(reply.schema!=="motionwright.narration-impact-preview/1"||reply.owner_approval!=="REQUIRED")
   invalid("Narration source preview cannot approve or execute a replacement.");
 const impact=record(reply.impact,"Narration impact");
 const timeline=record(reply.timeline_review,"Narration dependency map");
 if(impact.previous_source_sha256!==original.exact_content_sha256||
    impact.next_project_revision!==project.revision||
    impact.previous_project_revision!==original.revision||
    timeline.project_id!==project.id||timeline.generation!==project.generation||
    timeline.current_revision!==project.revision||
    timeline.source_revision!==original.revision||
    timeline.exact_source_sha256!==impact.candidate_source_sha256||
    typeof impact.candidate_source_sha256!=="string"||!HEX.test(impact.candidate_source_sha256))
   invalid("Narration replacement receipt is stale, foreign or has a changed source digest.");
 noClaims(timeline,["project_mutated","media_rendered","audio_regenerated","human_approved"],"Narration dependency map");
 if(!Array.isArray(timeline.scene_ids_requiring_cut_review) ||
   !Array.isArray(timeline.scene_ids_requiring_broll_review) ||
   JSON.stringify(timeline.scene_ids_requiring_cut_review)!==
     JSON.stringify(project.scenes.map(scene=>scene.id)) ||
   JSON.stringify(timeline.scene_ids_requiring_broll_review)!==
     JSON.stringify(project.scenes.map(scene=>scene.id)))
   invalid("Narration dependency mapping cannot omit unverified downstream scenes.");
 return reply as unknown as NarrationReplacementResponse;
}
export async function nativeNarrationSourceSnapshot(project:Project):Promise<NarrationSourceResponse>{
 if(!desktop())throw new Error("Narration baseline requires canonical desktop source.");
 const result:unknown=await invoke("native_narration_take_snapshot",{request:scope(project)});
 return validateNarrationSourceReply(project,result);
}
export async function nativeNarrationReplacementImpact(
 project:Project,original:NarrationSourceSnapshot
):Promise<NarrationReplacementResponse>{
 if(!desktop())throw new Error("Narration impact requires canonical desktop source.");
 if(original.project_id!==project.id||original.generation!==project.generation||
   original.revision>=project.revision||!HEX.test(original.exact_content_sha256))
   throw new Error("Narration baseline is stale or belongs to another project.");
 const result:unknown=await invoke("native_narration_replacement_impact",{
   request:{...scope(project),original,expected_original_sha256:original.exact_content_sha256},
 });
 return validateNarrationImpactReply(project,original,result);
}
