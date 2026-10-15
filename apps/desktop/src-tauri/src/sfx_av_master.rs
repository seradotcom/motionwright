//! v0.5 original Semantic SFX native H.264/AAC delivery through the existing
//! installed Studio consent + preview-token + Semwright Driver Host boundary.
//! This deliberately does NOT create an alternate audio codec/render service,
//! or silently combine narration/music with a source-only SFX master.
use super::{
    AppState,
    av_master::canonical_av_request_id,
    effect_grants::{EffectKind, EffectScope},
    sanitized, selected_connection_path,
};
use motionwright_domain::RevisionStamp;
use motionwright_native::production::{
    MltAvMasterEvidence, MltAvMasterRequest, ProductionConnection, ProductionCoordinator,
    SemanticSfxAvSourceProof, prepare_semantic_sfx_av_audio,
};
use serde::{Deserialize, Serialize};
use tauri::State;
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssembleSemanticSfxAvRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    effect_grant: Uuid,
    deliverable_id: Uuid,
    preview_token: Uuid,
}

/// A measured-voice or multisegment master cannot silently drop scene-
/// authored SFX. Existing voice-only behavior is unchanged for projects
/// without semantic SFX; a combined master requires a separately approved
/// source-bound mixing path.
pub(crate) fn reject_unmixed_semantic_sfx(
    project: &motionwright_domain::Project,
) -> Result<(), String> {
    if !project.audio.sfx_cues.is_empty() {
        return Err(
            "This voice-only native AV route cannot omit authored semantic SFX. Use a separately verified combined-audio master or explicitly remove the SFX cues before voice-only mastering.".into()
        );
    }
    Ok(())
}

/// One audited source-only receipt, separate from measured voice mastering.
/// The embedded source proof is not creative approval or a path capability.
#[derive(Debug, Serialize)]
pub struct NativeSemanticSfxAvResponse {
    #[serde(flatten)]
    evidence: MltAvMasterEvidence,
    export_token: Option<Uuid>,
    semantic_sfx_source_proof: SemanticSfxAvSourceProof,
    semantic_sfx_cue_count: usize,
    semantic_sfx_human_creative_approval: &'static str,
}

#[tauri::command]
pub async fn assemble_semantic_sfx_av_master(
    state: State<'_, AppState>,
    request: AssembleSemanticSfxAvRequest,
) -> Result<NativeSemanticSfxAvResponse, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("The SFX master belongs to a stale creative revision.".into());
    }
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == request.deliverable_id)
        .ok_or_else(|| "A saved project output profile is required for native SFX.".to_owned())?;
    if profile.frame_rate.num != 30
        || profile.frame_rate.den != 1
        || profile.audio_sample_rate_hz != 48_000
        || profile.video_codec != motionwright_domain::VideoCodec::H264
        || profile.audio_codec != motionwright_domain::AudioCodec::Aac
        || profile.container != motionwright_domain::OutputContainer::Mp4
        || profile.color_space != motionwright_domain::OutputColorSpace::Rec709
        || !profile.included_scene_ids.is_empty()
    {
        return Err(
            "Native source-bound SFX currently requires full-scene 30 fps, 48 kHz stereo, Rec.709 and H.264/AAC MP4.".into()
        );
    }
    if profile.voice_track_id.is_some() {
        return Err("This source-only SFX master does not mix measured voice or music. Select a profile with no voice track.".into());
    }
    if project.audio.sfx_cues.is_empty() {
        return Err("Author at least one source-bound original SFX event before requesting a native master.".into());
    }

    // Only a real render that this desktop registered can mint this handle.
    // No WebView-supplied filesystem paths, MLT jobs or arbitrary Film JSON.
    let motion =
        state
            .preview
            .master_source(&project, request.preview_token, request.deliverable_id)?;
    let segment = motion
        .segments
        .first()
        .ok_or_else(|| "The source has no authenticated native Film segment.".to_owned())?;
    if motion.segments.len() != 1
        || motion.frame_rate.num != 30
        || motion.frame_rate.den != 1
        || !(1..=900).contains(&segment.frame_count)
    {
        return Err(
            "Native SFX mastering requires one source-bound Film run of 1–900 frames at 30 fps."
                .into(),
        );
    }
    let connection_path =
        selected_connection_path(&state, request.project_id)?.ok_or_else(|| {
            "Choose the owner-provisioned Semwright connection before exporting SFX.".to_owned()
        })?;
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "The Semwright source connection could not be verified.".to_owned())?;
    if connection.resource != project.resource_key() {
        return Err("Native SFX output authority belongs to another project resource.".into());
    }
    state.preview.require_semantic_sfx_source_root(
        &project,
        request.preview_token,
        request.deliverable_id,
        &connection.output_root,
    )?;

    // One-time owner grant gates both the new WAV artifact and the final
    // native Broker mutation. A stale/forged handle is rejected beforehand.
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        request.request_id.trim(),
    )?;
    let expected = RevisionStamp {
        resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
    };
    let service = state.service.clone();
    let source = expected.clone();
    let id = request.project_id;
    let profile_id = request.deliverable_id;
    let frames = segment.frame_count;
    let root = connection.output_root.clone();
    // WAV creation reads the actual original StudioService CAS bytes. It is
    // bounded/blocking and never runs inside the desktop async executor.
    let prepared = tauri::async_runtime::spawn_blocking(move || {
        prepare_semantic_sfx_av_audio(&service, id, &source, profile_id, frames, &root)
    })
    .await
    .map_err(|_| "The original PCM preparation worker was interrupted.".to_string())?
    .map_err(|_| "The source-bound SFX mix could not be verified or staged.".to_string())?;
    let canonical_request_id = canonical_av_request_id(
        project.id,
        project.generation,
        project.revision,
        request.deliverable_id,
        &format!("semantic-sfx:{}", segment.fingerprint),
        &prepared.audio.sha256,
    );
    let output_root = connection.output_root.clone();
    let coordinator = ProductionCoordinator::new(state.service.clone(), connection)
        .map_err(|_| "The native Semwright production connection was rejected.".to_owned())?;
    let evidence = coordinator.assemble_native_semantic_sfx_av_master(
        request.project_id,
        &expected,
        MltAvMasterRequest {
            request_id: &canonical_request_id,
            deliverable_id: request.deliverable_id,
            motion: &motion,
            audio: &prepared.audio,
            sync: None,
        },
    ).await.map_err(|error| {
        if error.outcome_known {
            "The native SFX master failed or was refused. Inspect the original production receipts; no verified MP4 was issued.".to_owned()
        } else {
            "Native SFX master outcome is unconfirmed. Reconcile the original job with Semwright before another attempt.".to_owned()
        }
    })?;
    if prepared.proof.exact_pcm_sha256 != evidence.source_audio.sha256 {
        return Err("The original SFX source receipt and final native master input differ.".into());
    }
    let export_token = state.av_delivery.register(&output_root, &evidence);
    Ok(NativeSemanticSfxAvResponse {
        evidence,
        export_token,
        semantic_sfx_source_proof: prepared.proof,
        semantic_sfx_cue_count: project.audio.sfx_cues.len(),
        semantic_sfx_human_creative_approval: "REQUIRED",
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn request() -> serde_json::Value {
        json!({
            "project_id":Uuid::new_v4(),
            "generation":Uuid::new_v4(),
            "revision":7,
            "request_id":"scoped-one-time-render",
            "effect_grant":Uuid::new_v4(),
            "deliverable_id":Uuid::new_v4(),
            "preview_token":Uuid::new_v4(),
        })
    }

    #[test]
    fn voice_only_route_rejects_existing_semantic_sfx_instead_of_muting_it() {
        let mut project = motionwright_domain::Project::new("Original audio edit").unwrap();
        assert!(reject_unmixed_semantic_sfx(&project).is_ok());
        project
            .audio
            .sfx_cues
            .push(motionwright_domain::SemanticSfxCue {
                schema: motionwright_domain::SFX_CUE_SCHEMA.into(),
                id: Uuid::new_v4(),
                scene_id: Uuid::new_v4(),
                event: motionwright_domain::SfxEvent::Transition,
                anchor: motionwright_domain::SfxAnchor::SceneStart,
                offset_frames: 0,
                source_asset_id: Uuid::new_v4(),
                source_sha256: "a".repeat(64),
                source_frames: 30,
                gain_milli_db: -12_000,
                attack_frames: 0,
                release_frames: 0,
                original_source_attestation: "Owned source".into(),
            });
        assert!(
            reject_unmixed_semantic_sfx(&project)
                .unwrap_err()
                .contains("cannot omit authored semantic SFX")
        );
    }

    #[test]
    fn semantic_sfx_master_accepts_only_opaque_owner_scoped_request_fields() {
        let canonical = request();
        let parsed: AssembleSemanticSfxAvRequest =
            serde_json::from_value(canonical.clone()).unwrap();
        assert_eq!(parsed.revision, 7);
        assert_eq!(parsed.request_id, "scoped-one-time-render");
        for forbidden in [
            "audio_path",
            "video_path",
            "voice_track_id",
            "source_sha256",
            "owner_output_root",
            "native_job_ref",
            "master_path",
        ] {
            let mut foreign = canonical.clone();
            foreign[forbidden] = json!("/private/media/path.wav");
            assert!(
                serde_json::from_value::<AssembleSemanticSfxAvRequest>(foreign).is_err(),
                "WebView must not supply {forbidden}"
            );
        }
        let mut missing = canonical;
        missing.as_object_mut().unwrap().remove("effect_grant");
        assert!(serde_json::from_value::<AssembleSemanticSfxAvRequest>(missing).is_err());
    }
}
