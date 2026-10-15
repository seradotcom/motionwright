import { fixtureProject } from "./fixture";
import type { Project } from "./types";
import type { OtioEditPreview } from "./otioCutReview";

export function reviewableOtioProject():Project{
  const project=structuredClone(fixtureProject);
  project.scenes.forEach(scene=>{scene.status="draft";});
  project.reviews=[];project.locks=[];
  return project;
}
export function sampleOtioPreview(project:Project):OtioEditPreview {
  const ids=project.scenes.map(scene=>scene.id);
  const properties=[
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
  ] as const;
  return {
    schema:"motionwright.otio-edit-preview/1",
    source_project_sha256:"f".repeat(64),
    project_id:project.id,generation:project.generation,
    expected_project_revision:project.revision,
    original_scene_order:ids,
    proposed_scene_order:[ids[1],ids[0],...ids.slice(2)],
    native_change_proposals:[
      {type:"move_scene",scene_id:ids[1],to_index:0},
      {type:"set_scene_duration",scene_id:ids[2],duration:{num:"8",den:"1"}},
    ],
    media_refs_verified:0,missing_references_retained:ids.length,
    loss_report:["Audio, effects, geometry and source identities remain original project state.",
      "No third-party files, source in-points or execution rights will be imported."],
    property_fidelity:properties.map(([property,status,import_state])=>
      property==="unbound_scene_media"
        ?{property,status,import_state,missing_count:ids.length}
        :{property,status,import_state}),
    edit_imported:false,source_media_relinked:false,owner_revision_commit_required:true,
    human_review_required:true,rendering_performed:false,publication_approved:false,
    semantic_native_roundtrip:"NOT_SUPPORTED",nle_receiver_acceptance:"NOT_VERIFIED",
  };
}
