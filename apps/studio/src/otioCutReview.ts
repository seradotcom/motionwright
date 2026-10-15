/** Source-bound, non-executable OTIO review contract. The Python bridge
 * verifies external media separately; JSON in Studio is only a proposal. */
import type { Project, RationalTime } from "./types";

export type OtioNativeCut =
  | { type: "move_scene"; scene_id: string; to_index: number }
  | { type: "set_scene_duration"; scene_id: string; duration: RationalTime };
export interface OtioPropertyFidelity {
  property: string; status: string; import_state: string; missing_count?: number;
}
export interface OtioEditPreview {
  schema: "motionwright.otio-edit-preview/1";
  source_project_sha256: string; project_id: string; generation: string;
  expected_project_revision: number; original_scene_order: string[];
  proposed_scene_order: string[]; native_change_proposals: OtioNativeCut[];
  media_refs_verified: number; missing_references_retained: number;
  loss_report: string[]; property_fidelity: OtioPropertyFidelity[];
  edit_imported: false; source_media_relinked: false; owner_revision_commit_required: true;
  human_review_required: true; rendering_performed: false;
  semantic_native_roundtrip: "NOT_SUPPORTED"; nle_receiver_acceptance: "NOT_VERIFIED";
  publication_approved: false;
}
export interface OtioOwnerCutReview {
  preview: OtioEditPreview;
  reviewer_declaration: string; rationale: string;
  acknowledges_property_losses: true;
  acknowledges_absolute_audio_marker_positions: boolean;
  confirms_single_revision_change: true;
}
const FIDELITY: ReadonlyArray<readonly [string, string, string]> = [
  ["scene_order","editable_otio","bounded_proposal_only"],
  ["scene_duration","editable_otio","bounded_frame_exact_end_trim_only"],
  ["source_video_media","verified_external_reference","source_sha_revalidated_no_new_media"],
  ["media_source_in_point","represented_in_otio","not_supported_native_scene_offset"],
  ["unbound_scene_media","missing_reference","cannot_invent_media"],
  ["native_object_geometry_hierarchy_text_masks","source_project_only","not_supported"],
  ["camera_keyframes_effects_and_procedural_controls","source_project_only","not_supported"],
  ["source_audio_and_voice_cues","source_project_only","not_roundtripped_as_nle_audio"],
  ["captions_transcripts_alignment","source_project_only","not_roundtripped"],
  ["human_locks_creative_reviews_branches","source_project_only","not_roundtripped"],
  ["runtime_render_authority","not_exported","requires_separate_owner_grant"],
  ["publication_rights","not_granted","requires_separate_owner_approval"],
];
const SHA = /^[0-9a-f]{64}$/;
const safeText = (value: unknown, max: number): value is string =>
  typeof value === "string" && !!value.trim() &&
  new TextEncoder().encode(value).length <= max && !/[\u0000-\u001f\u007f]/u.test(value);
const invalid = (message:string):never=>{throw new Error(message);};
const object = (value:unknown, keys:string[], where:string):Record<string,unknown> => {
  if(value===null || typeof value!=="object" || Array.isArray(value))return invalid(where+" must be an object.");
  const row=value as Record<string,unknown>;
  if(Object.keys(row).length!==keys.length || keys.some(key=>!Object.hasOwn(row,key)))
    return invalid(where+" has missing or unrecognized fields.");
  return row;
};
const boundedInt=(n:unknown,min:number,max:number)=>
  typeof n==="number" && Number.isSafeInteger(n) && n>=min && n<=max;
const durationFraction=(v:unknown):[bigint,bigint]=>{
  const row=object(v,["num","den"],"Rational duration");
  if(typeof row.num!=="string" || !/^[1-9][0-9]{0,12}$/.test(row.num) ||
     typeof row.den!=="string" || !/^[1-9][0-9]{0,12}$/.test(row.den))
    return invalid("OTIO tail trim must contain positive exact integer numerator/denominator strings.");
  return [BigInt(row.num),BigInt(row.den)];
};
const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);

export function inspectOtioCutPreview(project:Project, text:string):OtioEditPreview{
  if(text.length>128_000)return invalid("The bounded OTIO preview exceeds 128 KiB.");
  let decoded:unknown;
  try{decoded=JSON.parse(text);}catch{return invalid("Paste a complete JSON proposal from the bounded OTIO bridge.");}
  const keys=[
    "schema","source_project_sha256","project_id","generation","expected_project_revision",
    "original_scene_order","proposed_scene_order","native_change_proposals",
    "media_refs_verified","missing_references_retained","loss_report","property_fidelity",
    "edit_imported","source_media_relinked","owner_revision_commit_required","human_review_required",
    "rendering_performed","semantic_native_roundtrip","nle_receiver_acceptance","publication_approved",
  ];
  const row=object(decoded,keys,"OTIO preview");
  const sceneIds=project.scenes.map(scene=>scene.id);
  if(row.schema!=="motionwright.otio-edit-preview/1" ||
      row.project_id!==project.id || row.generation!==project.generation ||
      row.expected_project_revision!==project.revision ||
      typeof row.source_project_sha256!=="string" || !SHA.test(row.source_project_sha256) ||
      !Array.isArray(row.original_scene_order) || !same(row.original_scene_order,sceneIds) ||
      !Array.isArray(row.proposed_scene_order) ||
      row.proposed_scene_order.length!==sceneIds.length ||
      sceneIds.length===0 || sceneIds.length>32 ||
      new Set(row.proposed_scene_order).size!==sceneIds.length ||
      !row.proposed_scene_order.every(value=>typeof value==="string" && sceneIds.includes(value)))
    return invalid("OTIO proposal is stale, foreign or does not preserve all original scenes.");
  if(row.edit_imported!==false || row.source_media_relinked!==false ||
      row.owner_revision_commit_required!==true || row.human_review_required!==true ||
      row.rendering_performed!==false || row.publication_approved!==false ||
      row.semantic_native_roundtrip!=="NOT_SUPPORTED" ||
      row.nle_receiver_acceptance!=="NOT_VERIFIED")
    return invalid("An OTIO proposal cannot assert media import, renderer proof or publication authorization.");
  if(!boundedInt(row.media_refs_verified,0,32) ||
      !boundedInt(row.missing_references_retained,0,32) ||
      (row.media_refs_verified as number)+(row.missing_references_retained as number)!==sceneIds.length)
    return invalid("OTIO media and missing-reference counts do not cover the source scenes.");
  if(!Array.isArray(row.property_fidelity) || row.property_fidelity.length!==12 ||
      row.property_fidelity.some((field,i)=>{
        const expected=FIDELITY[i];
        if(!field || typeof field!=="object" || Array.isArray(field))return true;
        const obj=field as Record<string,unknown>;
        const fields=i===4?["property","status","import_state","missing_count"]:["property","status","import_state"];
        return Object.keys(obj).length!==fields.length || fields.some(key=>!Object.hasOwn(obj,key)) ||
          obj.property!==expected[0] || obj.status!==expected[1] || obj.import_state!==expected[2] ||
          (i===4 && obj.missing_count!==row.missing_references_retained);
      }))
    return invalid("The twelve original property-fidelity disclosures were changed or removed.");
  if(!Array.isArray(row.loss_report) || !row.loss_report.length || row.loss_report.length>64 ||
      row.loss_report.some(note=>!safeText(note,1600)))
    return invalid("OTIO loss descriptions must remain complete and bounded.");
  const operations=row.native_change_proposals;
  if(!Array.isArray(operations) || operations.length===0 || operations.length>64)
    return invalid("An OTIO review requires one or more bounded scene edits.");
  const trimmed=new Map<string,RationalTime>();
  for(const operation of operations){
    if(!operation || typeof operation!=="object" || Array.isArray(operation))
      return invalid("Unknown OTIO edit entry.");
    const op=operation as Record<string,unknown>;
    if(op.type==="move_scene"){
      object(op,["type","scene_id","to_index"],"Reorder");
      if(typeof op.scene_id!=="string" || !sceneIds.includes(op.scene_id) ||
          !boundedInt(op.to_index,0,sceneIds.length-1))return invalid("Reorder target is outside the project.");
    }else if(op.type==="set_scene_duration"){
      object(op,["type","scene_id","duration"],"Tail trim");
      if(typeof op.scene_id!=="string" || !sceneIds.includes(op.scene_id) ||
          trimmed.has(op.scene_id))return invalid("Duplicated or unknown tail trim.");
      const [num,den]=durationFraction(op.duration);
      const scene=project.scenes.find(s=>s.id===op.scene_id)!;
      const old=durationFraction(scene.duration);
      if(num*old[1]>=old[0]*den || num>600n*den)
        return invalid("OTIO cannot extend a scene, preserve an unchanged trim, or exceed 600 seconds.");
      trimmed.set(op.scene_id,op.duration as RationalTime);
    }else return invalid("This receiver supports only MoveScene and SetSceneDuration.");
  }
  const moves:OtioNativeCut[]=[];
  const working=[...sceneIds];
  for(let index=0;index<working.length;index++){
    const id=(row.proposed_scene_order as string[])[index];
    if(working[index]===id)continue;
    const old=working.indexOf(id);
    if(old<0)return invalid("Missing original scene.");
    working.splice(old,1);working.splice(index,0,id);
    moves.push({type:"move_scene",scene_id:id,to_index:index});
  }
  const canonical:OtioNativeCut[]=[...moves];
  for(const sceneId of row.proposed_scene_order as string[]){
    const duration=trimmed.get(sceneId);
    if(duration)canonical.push({type:"set_scene_duration",scene_id:sceneId,duration});
  }
  if(!same(canonical,operations))return invalid("OTIO operations are reordered, nonminimal, duplicated or changed.");
  if(project.scenes.some(scene=>scene.status==="approved" || scene.status==="review") ||
      project.reviews?.some(review=>review.status==="open" || review.status==="needs_recheck"))
    return invalid("Resolve existing creative review and scene approval before applying an external cut.");
  if(project.locks?.some(lock=>lock.resource===("project:"+project.id) &&
      ["content","timing"].includes(lock.kind)))
    return invalid("The project is content/timing locked.");
  for(const op of canonical){
    if(project.locks?.some(lock=>lock.resource===("scene:"+op.scene_id) &&
        (lock.kind==="timing" || (op.type==="move_scene" && lock.kind==="content"))))
      return invalid("The proposed edit targets a locked original scene.");
  }
  return row as unknown as OtioEditPreview;
}
export function otioHasAbsoluteAnchors(project:Project):boolean{
  return !!(project.markers.length || project.audio.voice_tracks.length ||
    project.audio.transcript.length || project.audio.cues.length ||
    (project.audio.sfx_cues?.length??0));
}
